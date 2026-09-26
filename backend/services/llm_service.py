import asyncio
import json
import logging
from fastapi import HTTPException
from google import genai
from google.genai import types
from google.genai.errors import APIError, ClientError
from groq import AsyncGroq
from config import GEMINI_API_KEYS, GEMINI_MODEL, GROQ_API_KEYS, GROQ_MODEL
from services.github_mcp import repository_mcp

logger = logging.getLogger(__name__)
clients = [genai.Client(api_key=key) for key in GEMINI_API_KEYS]
client = clients[0] if clients else None

groq_clients = [AsyncGroq(api_key=key) for key in GROQ_API_KEYS]
GROQ_FALLBACK_MODELS = [
    "llama-3.3-70b-versatile",
    "llama-3.1-8b-instant",
]
_groq_seen = set()
GROQ_FALLBACK_MODELS = [m for m in GROQ_FALLBACK_MODELS if not (m in _groq_seen or _groq_seen.add(m))]

DEFAULT_MODEL = GEMINI_MODEL
FALLBACK_MODELS = [
    DEFAULT_MODEL,
    "gemini-2.5-flash-lite",
    "gemini-3.6-flash",
    "gemini-3.5-flash-lite",
]
_seen = set()
FALLBACK_MODELS = [m for m in FALLBACK_MODELS if not (m in _seen or _seen.add(m))]


def _convert_contents_to_groq_messages(system_instruction, contents):
    messages = []
    if system_instruction:
        messages.append({"role": "system", "content": str(system_instruction)})

    for c in contents:
        role = getattr(c, "role", "user")
        if role == "model":
            role = "assistant"
        elif role == "tool":
            role = "user"
        
        parts_text = []
        for p in getattr(c, "parts", []):
            if hasattr(p, "text") and p.text:
                parts_text.append(p.text)
            elif hasattr(p, "function_response") and p.function_response:
                resp = p.function_response
                name = getattr(resp, "name", "tool")
                response_val = getattr(resp, "response", {})
                parts_text.append(f"Tool {name} result: {json.dumps(response_val)}")
        
        content_str = "\n".join(parts_text) if parts_text else ""
        if content_str:
            messages.append({"role": role, "content": content_str})

    return messages


def _adapt_groq_response(message):
    function_calls = []
    if hasattr(message, "tool_calls") and message.tool_calls:
        for tc in message.tool_calls:
            try:
                args = json.loads(tc.function.arguments) if isinstance(tc.function.arguments, str) else (tc.function.arguments or {})
            except Exception:
                args = {}
            function_calls.append(types.Part.from_function_call(name=tc.function.name, args=args).function_call)

    text = message.content or ""
    candidates = [types.Content(role="model", parts=[types.Part.from_text(text=text)])]
    from types import SimpleNamespace
    return SimpleNamespace(function_calls=function_calls, text=text, candidates=[SimpleNamespace(content=candidates[0])])


async def _try_groq_fallback(contents, config):
    if not groq_clients:
        return None

    system_instruction = getattr(config, "system_instruction", None)
    groq_messages = _convert_contents_to_groq_messages(system_instruction, contents)

    groq_tools = []
    if hasattr(config, "tools") and config.tools:
        for tool in config.tools:
            for decl in getattr(tool, "function_declarations", []):
                groq_tools.append({
                    "type": "function",
                    "function": {
                        "name": decl.name,
                        "description": decl.description or "",
                        "parameters": decl.parameters_json_schema or {"type": "object", "properties": {}},
                    }
                })

    for model in GROQ_FALLBACK_MODELS:
        for idx, gc in enumerate(groq_clients):
            try:
                kwargs = {
                    "model": model,
                    "messages": groq_messages,
                    "temperature": getattr(config, "temperature", 0.2),
                }
                if groq_tools:
                    kwargs["tools"] = groq_tools
                    kwargs["tool_choice"] = "auto"
                
                logger.info(f"[Agent] Attempting Groq fallback with key #{idx + 1} and model '{model}'...")
                res = await gc.chat.completions.create(**kwargs)
                message = res.choices[0].message
                return _adapt_groq_response(message)
            except Exception as exc:
                logger.warning(f"[Agent] Groq key #{idx + 1} model '{model}' failed: {exc}")
                continue

    return None


import os
import random
import time
from typing import Any

# Context & Token Budget Configuration
MAX_AGENT_TURNS = 6

MAX_INPUT_CHARS_PER_CALL = 120_000   # ~30,000 estimated input tokens per LLM call
MAX_TOTAL_CONTEXT_CHARS = 150_000    # ~37,500 estimated context tokens total
MAX_SOURCE_CHARS_PER_FILE = 16_000   # ~4,000 estimated tokens max per file
MAX_TOTAL_SOURCE_CHARS = 60_000      # ~15,000 estimated tokens total source across files
MAX_RETRIEVED_FILES = 10

_current_gemini_key_idx = 0
_gemini_key_exhausted_until = {}


def _calculate_content_chars(contents) -> int:
    total = 0
    if not contents:
        return total
    if not isinstance(contents, list):
        contents = [contents]
    for c in contents:
        for p in getattr(c, "parts", []):
            if hasattr(p, "text") and p.text:
                total += len(p.text)
            elif hasattr(p, "function_call") and p.function_call:
                total += len(str(p.function_call))
            elif hasattr(p, "function_response") and p.function_response:
                total += len(str(p.function_response))
    return total


def estimate_tokens(text_or_obj: Any) -> int:
    """
    Lightweight estimation helper for token count.
    Estimates ~4 characters per token for English text and source code.
    """
    if isinstance(text_or_obj, str):
        length = len(text_or_obj)
    elif isinstance(text_or_obj, (int, float)):
        length = int(text_or_obj)
    elif isinstance(text_or_obj, list):
        length = _calculate_content_chars(text_or_obj)
    else:
        length = len(str(text_or_obj or ""))
    return max(1, length // 4)


class ContextBudget:
    def __init__(
        self,
        max_source_per_file: int = MAX_SOURCE_CHARS_PER_FILE,
        max_total_source: int = MAX_TOTAL_SOURCE_CHARS,
        max_files: int = MAX_RETRIEVED_FILES,
    ):
        self.max_source_per_file = max_source_per_file
        self.max_total_source = max_total_source
        self.max_files = max_files
        self.retrieved_sources = {}  # path -> content
        self.total_source_chars = 0
        self.llm_calls = 0
        self.total_input_chars = 0
        self.total_output_chars = 0

    def can_add_source(self, path: str, content: str) -> tuple[bool, str, str]:
        """
        Check if source can be added under budget rules.
        Returns (allowed, status_code, processed_content).
        status_code: 'ok', 'duplicate', 'truncated', 'budget_truncated', 'budget_exceeded'
        """
        norm_path = path.strip()
        base_name = os.path.basename(norm_path)
        if norm_path in self.retrieved_sources or base_name in self.retrieved_sources:
            return False, "duplicate", content

        unique_files_count = len(set(self.retrieved_sources.values()))
        if unique_files_count >= self.max_files:
            return False, "budget_exceeded", ""

        orig_len = len(content)
        working_content = content
        is_file_truncated = False

        if orig_len > self.max_source_per_file:
            working_content = content[: self.max_source_per_file] + (
                f"\n\n[Source truncated by context budget.\n"
                f"Original size: {orig_len} chars.\n"
                f"Included: {self.max_source_per_file} chars.]"
            )
            is_file_truncated = True

        needed_len = len(working_content)
        remaining_budget = self.max_total_source - self.total_source_chars

        if remaining_budget <= 0:
            return False, "budget_exceeded", ""

        if needed_len > remaining_budget:
            if remaining_budget >= 1000:
                working_content = working_content[: remaining_budget] + (
                    f"\n\n[Source truncated by context budget.\n"
                    f"Original size: {orig_len} chars.\n"
                    f"Included: {remaining_budget} chars.]"
                )
                return True, "budget_truncated", working_content
            else:
                return False, "budget_exceeded", ""

        status = "truncated" if is_file_truncated else "ok"
        return True, status, working_content

    def add_source(self, path: str, content: str, steps: list = None) -> tuple[bool, str, str]:
        allowed, status, processed_content = self.can_add_source(path, content)
        norm_path = path.strip()
        base_name = os.path.basename(norm_path)

        if status == "duplicate":
            logger.info(f"[Context] Duplicate skipped: {norm_path}")
            if steps is not None:
                step_msg = f"[Context] Duplicate skipped: {norm_path}"
                if step_msg not in steps:
                    steps.append(step_msg)
            existing_content = self.retrieved_sources.get(norm_path) or self.retrieved_sources.get(base_name) or content
            return False, status, existing_content

        if not allowed:
            logger.info(f"[Context] Source skipped: {norm_path} | budget exceeded")
            if steps is not None:
                step_msg = f"[Context] Source skipped: {norm_path} | budget exceeded"
                if step_msg not in steps:
                    steps.append(step_msg)
            return False, status, ""

        self.retrieved_sources[norm_path] = processed_content
        self.retrieved_sources[base_name] = processed_content
        self.total_source_chars += len(processed_content)

        if status in ("truncated", "budget_truncated"):
            logger.info(f"[Context] Truncated source: {norm_path}")
            if steps is not None:
                step_msg = f"[Context] Truncated source: {norm_path}"
                if step_msg not in steps:
                    steps.append(step_msg)
        else:
            logger.info(f"[Context] Source accepted: {norm_path} | chars={len(processed_content)}")

        return True, status, processed_content

    def remaining_chars(self) -> int:
        return max(0, self.max_total_source - self.total_source_chars)

    def estimated_tokens(self) -> int:
        return estimate_tokens(self.total_source_chars)

    def summary(self) -> dict:
        unique_files = len(set(self.retrieved_sources.values()))
        return {
            "llm_calls": self.llm_calls,
            "source_files": unique_files,
            "source_chars": self.total_source_chars,
            "estimated_input_tokens": estimate_tokens(self.total_input_chars),
            "estimated_output_tokens": estimate_tokens(self.total_output_chars),
        }


def compact_contents_if_needed(contents, max_chars=MAX_INPUT_CHARS_PER_CALL):
    curr_chars = _calculate_content_chars(contents)
    if curr_chars <= max_chars:
        return contents

    logger.info(f"[Context] Compacting contents from {curr_chars} chars (max limit {max_chars})...")
    compacted = []

    # Reserve room for structural tags/headers (~500 chars)
    target_part_len = max(100, max_chars - 1000)

    for item in contents:
        parts = getattr(item, "parts", [])
        new_parts = []
        for p in parts:
            if hasattr(p, "text") and p.text and len(p.text) > target_part_len:
                truncated_text = p.text[:target_part_len] + "\n\n[Content truncated by context budget]"
                new_parts.append(types.Part.from_text(text=truncated_text))
            else:
                new_parts.append(p)
        role = getattr(item, "role", "user")
        compacted.append(types.Content(role=role, parts=new_parts))

    new_chars = _calculate_content_chars(compacted)
    if new_chars > max_chars and len(compacted) > 2:
        keep = [compacted[0]]
        for item in compacted[1:]:
            parts = getattr(item, "parts", [])
            has_source = any(
                hasattr(p, "function_response")
                and p.function_response
                and isinstance(getattr(p.function_response, "response", {}), dict)
                and "content" in getattr(p.function_response, "response", {})
                and len(str(getattr(p.function_response, "response", {}).get("content", ""))) > 300
                for p in parts
            )
            has_calls = any(hasattr(p, "function_call") and p.function_call for p in parts)
            has_mcp_text = any(hasattr(p, "text") and p.text and "MCP Tool" in p.text for p in parts)
            if has_source or has_calls or has_mcp_text:
                keep.append(item)
        compacted = keep

    final_chars = _calculate_content_chars(compacted)
    logger.info(f"[Context] Compacted contents to {final_chars} chars.")
    return compacted


def normalize_contents_for_model(contents, model_name: str):
    """
    Normalizes `contents` when target model does not support role='tool' (e.g. gemini-3.6-flash or other models).
    Preserves all function call parameters, tool responses, directory discoveries, and retrieved source code
    by mapping `tool` role messages into `user` role messages with formatted text representations.
    """
    if "3.6" not in model_name and "gemini-3.6" not in model_name:
        return contents

    logger.info(f"[LLM] Normalizing tool messages for fallback model '{model_name}'...")
    normalized = []
    for c in contents:
        role = getattr(c, "role", "user")
        parts = getattr(c, "parts", [])
        
        if role == "tool":
            new_parts = []
            for p in parts:
                if hasattr(p, "function_response") and p.function_response:
                    resp = p.function_response
                    name = getattr(resp, "name", "tool")
                    response_val = getattr(resp, "response", {})
                    if isinstance(response_val, dict) and "content" in response_val:
                        val_str = str(response_val["content"])
                    else:
                        val_str = json.dumps(response_val)
                    new_parts.append(types.Part.from_text(text=f"MCP Tool '{name}' result:\n{val_str}"))
                elif hasattr(p, "text") and p.text:
                    new_parts.append(types.Part.from_text(text=p.text))
            
            if not new_parts:
                new_parts = [types.Part.from_text(text="[Tool response completed]")]
            normalized.append(types.Content(role="user", parts=new_parts))
        else:
            normalized.append(c)

    return compact_contents_if_needed(normalized)


async def _generate_content_with_fallback(contents, config, reason="reasoning", call_count=1):
    global _current_gemini_key_idx
    contents = compact_contents_if_needed(contents)

    active_clients = clients
    if client is not None and (not isinstance(client, genai.Client) or not clients):
        active_clients = [client]

    input_chars = _calculate_content_chars(contents)
    est_in_tokens = estimate_tokens(input_chars)
    last_exc = None
    if active_clients:
        n_clients = len(active_clients)
        now = time.time()
        for model in FALLBACK_MODELS:
            model_contents = normalize_contents_for_model(contents, model)
            for offset in range(n_clients):
                idx = (_current_gemini_key_idx + offset) % n_clients
                c = active_clients[idx]

                key_model_tuple = (idx, model)
                if now < _gemini_key_exhausted_until.get(key_model_tuple, 0):
                    continue

                try:
                    res = await c.aio.models.generate_content(
                        model=model,
                        contents=model_contents,
                        config=config,
                    )
                    _current_gemini_key_idx = idx
                    out_text = _safe_response_text(res) or ""
                    out_chars = len(out_text) if out_text else len(str(res.function_calls or ""))
                    est_out_tokens = estimate_tokens(out_chars)
                    logger.info(
                        f"[LLM] Gemini {model} | call={call_count} | reason={reason}\n"
                        f"      input_chars={input_chars}\n"
                        f"      estimated_input_tokens={est_in_tokens}\n"
                        f"      output_chars={out_chars}\n"
                        f"      estimated_output_tokens={est_out_tokens}"
                    )
                    return res
                except (ClientError, APIError) as exc:
                    err_str = str(exc)
                    err_msg = (exc.message if hasattr(exc, "message") else str(exc)).lower()
                    code = getattr(exc, "code", None)
                    is_unavailable = (
                        "429" in err_str
                        or "503" in err_str
                        or "500" in err_str
                        or "502" in err_str
                        or "504" in err_str
                        or code in (429, 500, 502, 503, 504)
                        or "resource_exhausted" in err_msg
                        or "unavailable" in err_msg
                        or "overloaded" in err_msg
                        or "quota" in err_msg
                    )
                    if is_unavailable:
                        backoff = 2.0 if ("503" in err_str or "unavailable" in err_msg or code == 503) else (60.0 + random.uniform(0.1, 0.5))
                        logger.warning(
                            f"[Agent] Gemini Key #{idx + 1} model '{model}' hit error ({exc}). Backing off model for {backoff:.1f}s. Retrying next key/model or fallback..."
                        )
                        _gemini_key_exhausted_until[key_model_tuple] = time.time() + backoff
                        last_exc = exc
                        continue

                    # Non-transient errors (e.g. 400 Bad Request) raise immediately
                    logger.error(
                        f"[Agent] Gemini Key #{idx + 1} model '{model}' non-transient error: {exc}"
                    )
                    raise HTTPException(
                        502,
                        f"Gemini API error ({model}): {exc.message if hasattr(exc, 'message') else str(exc)}",
                    ) from None
                except Exception as exc:
                    logger.warning(
                        f"[Agent] Gemini Key #{idx + 1} model '{model}' unexpected failure: {exc}"
                    )
                    last_exc = exc
                    continue

    logger.warning("[Agent] All Gemini API keys/models failed or unconfigured. Attempting Groq fallback...")
    groq_res = await _try_groq_fallback(contents, config)
    if groq_res:
        out_text = _safe_response_text(groq_res) or ""
        out_chars = len(out_text) if out_text else len(str(groq_res.function_calls or ""))
        est_out_tokens = estimate_tokens(out_chars)
        logger.info(
            f"[LLM] Groq Fallback | call={call_count} | reason={reason}\n"
            f"      input_chars={input_chars}\n"
            f"      estimated_input_tokens={est_in_tokens}\n"
            f"      output_chars={out_chars}\n"
            f"      estimated_output_tokens={est_out_tokens}"
        )
        return groq_res

    logger.error(
        f"[Agent] All LLM providers (Gemini & Groq) exhausted or failed: {last_exc}"
    )
    raise HTTPException(
        429,
        "All LLM providers (Gemini & Groq) exceeded quota or failed. Please try again later.",
    ) from None


async def answer_question_with_mcp(user_id, owner, repo, question, history=None):
    """Gemini plans reads; the request's MCP client enforces their scope."""
    if not clients:
        raise HTTPException(503, "Gemini is not configured")

    logger.info("[Agent] User question received for %s/%s: %s", owner, repo, question)
    logger.info("[Agent] Determining required tools...")

    contents = []
    for msg in (history or []):
        m = msg if isinstance(msg, dict) else msg.model_dump()
        role = "user" if m.get("role") == "user" else "model"
        text = m.get("content", "")
        if text and text.strip():
            contents.append(types.Content(role=role, parts=[types.Part.from_text(text=text.strip())]))

    current_prompt = (
        f"Target Repository: '{owner}/{repo}'\n"
        f"User Question: {question}\n\n"
        "IMPORTANT: Execute `get_file_contents` or other MCP tools to inspect any specific files or routes "
        "mentioned in the question (such as assistantRoutes.js or assistantController.js) right now."
    )
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=current_prompt)]))
    steps = []

    async with asyncio.timeout(180):
        async with repository_mcp(user_id, owner, repo) as mcp:
            tools = await mcp.discover()
            tool_names = [t.name for t in tools]
            logger.info("[Agent] MCP tools discovered: %s", tool_names)

            declarations = [
                types.FunctionDeclaration(
                    name=tool.name,
                    description=tool.description,
                    parameters_json_schema=tool.inputSchema,
                )
                for tool in tools
            ]

            instruction = (
                f"You are GiTMCP, an AI code assistant analyzing the GitHub repository '{owner}/{repo}'. "
                f"Every user question is specifically about this repository. "
                "You have access to the repository through LIVE GitHub MCP tools. "
                "\n\n"
                "CORE RULES:"
                "\n1. For repository exploration questions, you MUST discover the repository structure before requesting any source file contents. Never guess filenames or construct paths based on common naming conventions."
                "\n2. You MUST inspect the repository with MCP tools before answering repository-specific questions."
                "\n3. MCP file retrieval returns ACTUAL SOURCE CODE. You MUST analyze the source code itself, not merely filenames or paths."
                "\n4. When answering code-related questions, inspect the retrieved source carefully. Identify functions, classes, routes, HTTP methods, parameters, imports, function calls, service calls, database operations, return values, and other relevant implementation details."
                "\n5. CROSS-FILE DEPENDENCY TRACKING: If a route/function calls another function, service, or repository module whose implementation is needed to answer the question, identify the file path and retrieve it using MCP."
                "\n6. BATCH TOOL CALLS: Whenever you need to inspect multiple files or directories, issue ALL function calls in a single turn at once using parallel function calling, rather than requesting them one by one across separate turns."
                "\n7. Never invent filenames or functions. Never claim to understand implementation logic based only on a filename. Use only source code actually retrieved through MCP."
                "\n8. Continue retrieving relevant files until there is enough evidence to answer the user's question completely."
                "\n9. Filenames are discovery evidence only, not implementation evidence. Never answer an API-analysis question using route filenames alone. Before describing API behavior, retrieve and inspect the source code of the relevant route files using MCP."
                "\n10. Previous assistant responses are not repository evidence. Always perform fresh MCP inspection when the current question requires source-code information."
                "\n11. Never say you cannot access the repository when MCP tools are available."
                "\n12. FOLLOW-UP RE-EVALUATION RULE: If previous history mentions that a file (e.g. `assistantRoutes.js` or `assistantController.js`) could not be retrieved, NEVER repeat that limitation. In this turn, you MUST call `get_file_contents` to fetch and analyze the file now."
                "\n\n"
                "API ANALYSIS & CODE FLOW:"
                "\nWhen the user asks questions such as:"
                "\n- 'What APIs does this repo have?'"
                "\n- 'What APIs are there and what do they do?'"
                "\n- 'Explain the APIs in this project including method, path, request, processing, dependencies, and response.'"
                "\n\nDO NOT simply list route filenames."
                "\nThose filenames are only the starting point."
                "\n\nYou MUST perform the following investigation:"
                "\n1. Discover the repository structure to find the actual API entry point and route files."
                "\n2. Read the relevant route files using get_file_contents."
                "\n3. Identify the actual HTTP method and endpoint path for each route."
                "\n4. Identify the controller/handler/service called by each route."
                "\n5. If the route delegates to a service, repository, or utility file, retrieve that dependency file using get_file_contents to trace the full execution flow."
                "\n6. Determine what the endpoint actually does from the implementation code."
                "\n7. Determine important request parameters, path parameters, query parameters, and request body fields."
                "\n8. Determine database queries, external service calls, or internal business logic."
                "\n9. Determine the response structure and output."
                "\n10. Synthesize the findings into a clear, comprehensive, code-backed explanation."
                "\n\nAPI RESPONSE FORMAT:"
                "\nStart with a short overview of the backend API architecture."
                "\nThen group endpoints logically."
                "\nFor each endpoint provide:"
                "\n- Method + path"
                "\n- Purpose"
                "\n- Request/input parameters"
                "\n- Processing & dependencies (trace calls into services/repositories)"
                "\n- Response/output structure"
                "\n- Relevant source files"
                "\n\nDo not fabricate missing information. "
                "If the implementation does not reveal a detail, explicitly say that it could not be determined."
                "\n\n"
                "DEPTH RULE:"
                "\nFor API and code questions, continue calling MCP tools until you have enough evidence "
                "to explain the complete functionality rather than merely identify the endpoint. "
                "Follow the implementation across files as deeply as required to answer the user's question."
                "\n\n"
                "PATH RESOLUTION:"
                "\nWhen calling get_file_contents, specify the full relative repository path (e.g. backend/app/routes/payments.py)."
                "\nIf you discover route files, retrieve each file's source code individually using get_file_contents."
                "\n\n"
                "FINAL ANSWER:"
                "\nAfter gathering repository evidence, synthesize a clear developer-focused answer based strictly on the retrieved source code."
                "\nDo not expose internal reasoning."
                "\nUse headings, bullets, and code snippets when they improve readability."
                "\nMention relevant repository file paths as evidence."
            )

            import os
            import re

            route_patterns = [
                r'(?:^|/)(?:routes|route|controllers|controller|api|endpoints|routers|router|handlers|handler)(?:/|$)',
                r'(?:route|routes|controller|controllers|router|routers|endpoint|endpoints|handler|handlers)\.(?:py|js|ts|go|java|rb|php|cs)$',
            ]

            def is_route_path(p: str) -> bool:
                p_clean = p.lower().replace("\\", "/")
                for pat in route_patterns:
                    if re.search(pat, p_clean):
                        return True
                return False

            known_paths = {}
            discovered_paths = {"README.md"}
            repository_discovered = False
            discovered_route_files = set()
            inspected_route_files = set()
            retrieved_dependency_files = set()
            q_lower = question.lower()
            is_api_question = any(kw in q_lower for kw in ["api", "apis", "endpoint", "endpoints", "route", "routes", "controller", "controllers"])

            def _is_file(p: str) -> bool:
                return bool(re.search(r'\.[a-zA-Z0-9]+$', p))

            budget = ContextBudget()
            guard_count = 0
            llm_call_count = 0
            for turn_idx in range(MAX_AGENT_TURNS):
                logger.info("[Agent] LLM reasoning turn %d...", turn_idx + 1)
                llm_call_count += 1
                reason = "discovery" if turn_idx == 0 else ("guard_reprompt" if guard_count > 0 else "reasoning")
                response = await _generate_content_with_fallback(
                    contents=contents,
                    config=types.GenerateContentConfig(
                        system_instruction=instruction,
                        tools=[types.Tool(function_declarations=declarations)],
                        temperature=0.2,
                        automatic_function_calling=types.AutomaticFunctionCallingConfig(
                            disable=True
                        ),
                    ),
                    reason=reason,
                    call_count=llm_call_count,
                )
                calls = response.function_calls or []

                if not calls:
                    text = _safe_response_text(response)
                    # If Gemini on turn 0 returned a refusal message like "I cannot answer..." without calling tools,
                    # force a fallback tool call (e.g. get_file_contents for README.md or list_commits)
                    if (
                        turn_idx == 0
                        and text
                        and (
                            "cannot" in text.lower()
                            or "do not have" in text.lower()
                            or "unable" in text.lower()
                        )
                    ):
                        logger.warning(
                            "[Agent] Model returned initial refusal text without tool calls. Attempting fallback tool call..."
                        )
                        fallback_tool = (
                            "get_file_contents"
                            if "get_file_contents" in mcp.tools
                            else (tool_names[0] if tool_names else None)
                        )
                        if fallback_tool:
                            logger.info(
                                "[Agent] MCP tool selected (fallback): %s",
                                fallback_tool,
                            )
                            fallback_args = (
                                {"path": "README.md"}
                                if fallback_tool == "get_file_contents"
                                else {}
                            )
                            res = await mcp.call(fallback_tool, fallback_args)
                            steps.append(f"Read GitHub via MCP: README.md")
                            repository_discovered = True
                            content_text = res.get("content", "") if isinstance(res, dict) else str(res)
                            budget.add_source("README.md", content_text, steps)
                            model_content = (
                                response.candidates[0].content
                                if (
                                    hasattr(response, "candidates")
                                    and response.candidates
                                )
                                else types.Content(
                                    role="model",
                                    parts=[types.Part.from_text(text=text)],
                                )
                            )
                            contents.append(model_content)
                            contents.append(
                                types.Content(
                                    role="tool",
                                    parts=[
                                        types.Part.from_function_response(
                                            name=fallback_tool, response=res
                                        )
                                    ],
                                )
                            )
                            continue

                    # Readiness check for API analysis and source retrieval
                    retrieved_route_sources = [f for f in inspected_route_files if "/" in f or _is_file(f)]
                    total_source_files_retrieved = len(set(budget.retrieved_sources.values()))

                    def api_analysis_ready() -> bool:
                        if not repository_discovered:
                            return False
                        if is_api_question:
                            if len(discovered_route_files) == 0:
                                return True
                            return len(retrieved_route_sources) > 0 or total_source_files_retrieved > 0
                        return True

                    ready = api_analysis_ready()
                    discovered_dirs_count = len({p for p in discovered_paths if not _is_file(p)})
                    logger.info(
                        f"[Agent] Repository discovered: {repository_discovered}\n"
                        f"[Agent] Route directories discovered: {discovered_dirs_count}\n"
                        f"[Agent] Route files discovered: {len(discovered_route_files)}\n"
                        f"[Agent] Route source files retrieved: {len(retrieved_route_sources)}\n"
                        f"[Agent] Dependency files retrieved: {len(retrieved_dependency_files)}\n"
                        f"[Agent] API analysis ready: {ready}"
                    )

                    # Guard: If route files were discovered but zero route source files were retrieved, DO NOT synthesize final answer yet.
                    uninspected_files = {f for f in discovered_route_files if "/" in f} - {f for f in inspected_route_files if "/" in f}
                    if is_api_question and discovered_route_files and not inspected_route_files and uninspected_files and guard_count < 3:
                        guard_count += 1
                        logger.warning(
                            "[Agent] API Guard triggered (%d/3): ready=%s, discovered route files=%s, uninspected=%s. Re-prompting for discovery/retrieval.",
                            guard_count, ready, discovered_route_files, uninspected_files
                        )
                        files_str = ", ".join(sorted(uninspected_files))
                        guard_instruction = (
                            "SYSTEM GUARD: Directory listings and filenames are discovery evidence only, NOT source code. "
                            "Never answer an API-analysis question using directory listings or route filenames alone. "
                            f"You have discovered API route files ({files_str}), but you have NOT retrieved their actual source code using `get_file_contents`. "
                            "You MUST call `get_file_contents` for each discovered route file path right now."
                        )
                        model_content = (
                            response.candidates[0].content
                            if (hasattr(response, "candidates") and response.candidates)
                            else types.Content(role="model", parts=[types.Part.from_text(text=text or "")])
                        )
                        contents.append(model_content)
                        contents.append(types.Content(role="user", parts=[types.Part.from_text(text=guard_instruction)]))
                        continue

                    if is_api_question and not ready:
                        logger.warning("[Agent] Cannot complete API analysis because ready=False (no route source code retrieved).")
                        if turn_idx > 0:
                            text = "I could not retrieve the repository source files needed to analyze the APIs."
                        else:
                            raise HTTPException(
                                502, "Repository API source files could not be retrieved for analysis."
                            )

                    if not text:
                        if turn_idx > 0:
                            logger.warning("[Agent] Model returned empty text on turn %d.", turn_idx + 1)
                            if total_source_files_retrieved > 0:
                                text = "Code retrieval completed, but final source-code analysis failed. Please ask a more focused question."
                            else:
                                text = "I could not retrieve the repository source files needed to analyze the APIs."
                        else:
                            raise HTTPException(
                                502, "The model returned no answer. Please try again."
                            )

                    # Only emit analysis steps if source code was actually retrieved and analyzed
                    if ready and total_source_files_retrieved > 0:
                        if is_api_question and "Analyzing endpoint behavior" not in steps:
                            steps.append("Analyzing endpoint behavior")
                        if "Final code analysis completed" not in steps and "retrieved and inspected via MCP" not in text:
                            steps.append("Final code analysis completed")

                    logger.info("[Agent] LLM synthesis completed")
                    logger.info("[Agent] Final response generated")
                    budget.llm_calls = llm_call_count
                    unique_files_count = len(set(budget.retrieved_sources.values()))
                    logger.info(
                        f"[Context] Final synthesis budget:\n"
                        f"source_files={unique_files_count}\n"
                        f"source_chars={budget.total_source_chars}\n"
                        f"input_chars={_calculate_content_chars(contents)}\n"
                        f"estimated_input_tokens={estimate_tokens(contents)}"
                    )
                    logger.info(
                        f"[Agent] Context summary:\n"
                        f"LLM calls={llm_call_count}\n"
                        f"source_files={unique_files_count}\n"
                        f"source_chars={budget.total_source_chars}\n"
                        f"estimated_input_tokens={estimate_tokens(_calculate_content_chars(contents))}\n"
                        f"estimated_output_tokens={estimate_tokens(len(text))}"
                    )
                    return {
                        "answer": text.replace(mcp._token, "[REDACTED]"),
                        "steps": steps,
                    }

                if len(calls) > 35:
                    raise HTTPException(
                        502, "Too many parallel tool requests (35 max per turn). Ask a more focused question."
                    )

                contents.append(response.candidates[0].content)
                responses = []
                for call in calls:
                    args = dict(call.args or {})
                    # Path resolution for get_file_contents or other file-based calls
                    if call.name == "get_file_contents" and "path" in args:
                        orig_path = str(args["path"]).strip()
                        if orig_path == "/":
                            orig_path = ""
                        elif orig_path.startswith("/"):
                            orig_path = orig_path[1:]
                        resolved_path = orig_path
                        if orig_path in known_paths:
                            resolved_path = known_paths[orig_path]
                        elif os.path.basename(orig_path) in known_paths:
                            resolved_path = known_paths[os.path.basename(orig_path)]
                        args["path"] = resolved_path
                        
                        logger.info(f"[Agent] MCP retrieval requested: {resolved_path}")

                        is_req_file = _is_file(resolved_path)
                        if is_req_file:
                            if not repository_discovered and resolved_path.lower() != "readme.md":
                                msg = "Discovery required before file retrieval"
                                logger.warning(f"[Agent] {msg}")
                                if msg not in steps:
                                    steps.append(msg)
                                result = {"error": "Repository structure has not been discovered yet. Discover the repository structure first, then request files using paths returned by discovery."}
                                responses.append(
                                    types.Part.from_function_response(
                                        name=call.name, response=result
                                    )
                                )
                                continue
                                
                            if resolved_path not in discovered_paths and os.path.basename(resolved_path) not in discovered_paths:
                                msg = f"Blocked unverified path: {resolved_path}"
                                logger.warning(f"[Agent] {msg}")
                                logger.warning(f"[Agent] Blocked unverified path: {resolved_path}")
                                if msg not in steps:
                                    steps.append(msg)
                                result = {"error": f"Path '{resolved_path}' has not been discovered. You must first use directory listing to find valid files before retrieving their contents."}
                                responses.append(
                                    types.Part.from_function_response(
                                        name=call.name, response=result
                                    )
                                )
                                continue
                            
                        step_msg = f"Read GitHub via MCP: {resolved_path}"
                        if step_msg not in steps:
                            steps.append(step_msg)
                    else:
                        req_p_log = str(args.get("path", call.name))
                        logger.info(f"[Agent] MCP retrieval requested: {req_p_log}")
                        step_msg = f"Read GitHub via MCP: {call.name}"
                        if step_msg not in steps:
                            steps.append(step_msg)

                    logger.info("[Agent] MCP tool selected: %s args=%s", call.name, args)
                    result = await mcp.call(call.name, args)
                    logger.info("[Agent] MCP tool executed: %s, result received", call.name)

                    content_text = result.get("content", "") if isinstance(result, dict) else str(result)
                    req_p = str(args.get("path", "")).strip() if call.name == "get_file_contents" else ""
                    
                    # Safe debug logging as requested
                    logger.info(
                        "[Agent] MCP Call: tool=%s, args=%s, type=%s, keys=%s, content_len=%d, content_preview=%r",
                        call.name,
                        args,
                        type(result).__name__,
                        list(result.keys()) if isinstance(result, dict) else [],
                        len(content_text),
                        content_text[:200]
                    )

                    # Check if req_p is a directory or if content_text represents a directory listing
                    is_file = _is_file(req_p)
                    is_directory = (not is_file and bool(req_p)) or "DIRECTORY LISTING" in content_text or "type\": \"dir\"" in content_text or "type\": \"file\"" in content_text or (isinstance(result, list))

                    # Detect Not Found / Error responses
                    content_lower = content_text.lower()
                    is_error_response = (
                        content_text.startswith("Error") or
                        "could not complete" in content_lower or
                        "not found" in content_lower or
                        "404" in content_lower or
                        (isinstance(result, dict) and "error" in result)
                    )

                    if is_error_response:
                        err_step = f"Retrieval failed: {req_p or call.name}"
                        if err_step not in steps:
                            steps.append(err_step)
                        logger.warning("[Agent] MCP Tool returned an error or not-found response for %s", req_p or call.name)

                    elif is_directory:
                        repository_discovered = True
                        if "Repository discovered" not in steps:
                            steps.append("Repository discovered")
                        dir_name = req_p.rstrip('/') if req_p else "repository"
                        if req_p:
                            known_paths[req_p] = req_p
                            discovered_paths.add(req_p)
                        dir_step = f"Directory discovered: {dir_name}/"
                        if dir_name and dir_step not in steps:
                            steps.append(dir_step)

                        # Extract all files/dirs from JSON or directory listing text
                        extracted_items = []
                        raw_data = result if isinstance(result, list) else None
                        if raw_data is None:
                            try:
                                raw_data = json.loads(content_text)
                            except Exception:
                                pass
                        
                        if isinstance(raw_data, list):
                            for item in raw_data:
                                if isinstance(item, dict):
                                    n = item.get("name") or ""
                                    p = item.get("path") or ""
                                    t = item.get("type") or ""
                                    target = p if p else (f"{req_p.rstrip('/')}/{n}" if req_p else n)
                                    if target:
                                        extracted_items.append((target, t))

                        if not extracted_items:
                            matches = re.findall(r'[a-zA-Z0-9_\-./]+\.[a-zA-Z0-9]+', content_text)
                            for m_clean in matches:
                                m_clean = m_clean.strip("'\"`[]():,; \t\r\n")
                                if m_clean.lower() in ("json", "html_url", "git_url", "download_url"):
                                    continue
                                if "/" not in m_clean and req_p:
                                    full_m = f"{req_p.rstrip('/')}/{m_clean}"
                                else:
                                    full_m = m_clean
                                extracted_items.append((full_m, "file" if _is_file(full_m) else "dir"))

                        new_files = []
                        for full_m, item_type in extracted_items:
                            norm_item = full_m.strip()
                            known_paths[norm_item] = norm_item
                            known_paths[os.path.basename(norm_item)] = norm_item
                            discovered_paths.add(norm_item)
                            discovered_paths.add(os.path.basename(norm_item))
                            
                            if item_type == "file" or _is_file(norm_item):
                                if is_route_path(norm_item) or is_route_path(req_p):
                                    discovered_route_files.add(norm_item)
                                    discovered_route_files.add(os.path.basename(norm_item))
                                    new_files.append(norm_item)

                        if new_files:
                            disc_files_str = ", ".join(sorted(set(new_files)))
                            disc_step = f"Route files discovered: {disc_files_str}"
                            if disc_step not in steps:
                                steps.append(disc_step)

                            # Format response so Gemini explicitly recognizes this as a directory listing and NOT file source content
                            result = {
                                "content": (
                                    f"DIRECTORY LISTING FOR '{req_p}':\n"
                                    f"The path '{req_p}' is a DIRECTORY containing files: {disc_files_str}.\n"
                                    "IMPORTANT: This is a directory listing, NOT file source code. "
                                    f"You MUST now call `get_file_contents` for each individual route file path (e.g. get_file_contents(path='{new_files[0]}')) to inspect its implementation code."
                                )
                            }
                        elif "Resolved potential matches" in content_text and "matching files:" in content_text:
                            fuzzy_match = re.search(r'matching files:\s*(\[.*?\])', content_text)
                            if fuzzy_match:
                                import ast
                                try:
                                    matched_list = ast.literal_eval(fuzzy_match.group(1))
                                    if matched_list:
                                        dirs_str = ", ".join(matched_list)
                                        first_dir = matched_list[0].strip('/')
                                        result = {
                                            "content": (
                                                f"FUZZY MATCH FOR '{req_p}':\n"
                                                f"The requested path '{req_p}' matched the following actual repository paths: {dirs_str}.\n"
                                                f"IMPORTANT: You MUST now call `get_file_contents` again using one of these exact absolute paths (e.g. get_file_contents(path='{first_dir}')) to list its contents."
                                            )
                                        }
                                except Exception:
                                    pass
                    elif call.name == "get_file_contents" and req_p and is_file:
                        if is_route_path(req_p):
                            inspected_route_files.add(req_p)
                            inspected_route_files.add(os.path.basename(req_p))
                            discovered_route_files.add(req_p)
                        else:
                            retrieved_dependency_files.add(req_p)
                        
                        allowed, status, processed_content = budget.add_source(req_p, content_text, steps)
                        if status == "duplicate":
                            result = {"content": processed_content, "note": "Duplicate source file omitted from context payload."}
                        elif not allowed:
                            result = {"error": f"Source file '{req_p}' skipped because total context budget was exceeded."}
                        else:
                            result = {"content": processed_content, "truncated": status in ("truncated", "budget_truncated")}

                        logger.info("[Agent] Source retrieved: %s (Length: %d chars)", req_p, len(content_text))
                        logger.info(f"[Context] Current source chars={budget.total_source_chars}")
                        logger.info(f"[Context] Estimated context tokens={budget.estimated_tokens()}")
                        logger.info(f"[Context] Remaining budget={budget.remaining_chars()}")

                        src_step = f"Source retrieved: {req_p}"
                        if src_step not in steps:
                            steps.append(src_step)
                        
                        anz_step = f"Analyzing source: {req_p}"
                        if anz_step not in steps:
                            steps.append(anz_step)

                        # Extract potential cross-file dependencies from import/require statements in retrieved source
                        imp_matches = re.findall(r'(?:from|import|require)\s+[\'"]?([a-zA-Z0-9_\-./]+)[\'"]?', content_text)
                        for m_imp in imp_matches:
                            clean_imp = m_imp.replace(".", "/").strip("/")
                            if any(clean_imp.endswith(e) for e in [".py", ".js", ".ts", ".jsx", ".tsx"]):
                                candidate_paths = [clean_imp]
                            else:
                                candidate_paths = [f"{clean_imp}{ext}" for ext in [".py", ".js", ".ts", ".jsx", ".tsx"]]
                            for cand in candidate_paths:
                                known_paths[cand] = cand
                                known_paths[os.path.basename(cand)] = cand
                                discovered_paths.add(cand)
                                discovered_paths.add(os.path.basename(cand))
                        
                        is_dep = any(k in req_p.lower() for k in ["service", "controller", "repository", "model", "util", "helper"])
                        if is_dep:
                            dep_step = f"Dependency source retrieved: {req_p}"
                            if dep_step not in steps:
                                steps.append(dep_step)
                            cross_step = f"Cross-file analysis: {req_p}"
                            if cross_step not in steps:
                                steps.append(cross_step)
                            svc_base = os.path.basename(req_p).replace(".py", "").replace(".js", "").replace(".ts", "").replace("_", " ")
                            inspected_msg = f"Inspected {svc_base}"
                            if inspected_msg not in steps:
                                steps.append(inspected_msg)

                    responses.append(
                        types.Part.from_function_response(
                            name=call.name, response=result
                        )
                    )
                contents.append(types.Content(role="tool", parts=responses))

    if steps and repository_discovered:
        return {
            "answer": f"Analysis incomplete (reached {MAX_AGENT_TURNS} turn limit). The agent could not fully resolve the request within the turn limit. Here are the findings so far.",
            "steps": steps,
        }
    raise HTTPException(502, f"Reasoning turn limit reached ({MAX_AGENT_TURNS} turns max). Try asking about a specific subfolder or module.")


def _safe_response_text(response) -> str | None:
    """
    Safely extract text from a Gemini response.
    response.text raises if there are no candidates or the finish reason is not STOP.
    """
    try:
        text = response.text
        if text and text.strip():
            return text
    except Exception:
        pass

    # Fallback: manually walk candidates → parts
    try:
        for candidate in response.candidates or []:
            for part in candidate.content.parts or []:
                if hasattr(part, "text") and part.text and part.text.strip():
                    return part.text
    except Exception:
        pass

    return None
