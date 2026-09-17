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
    GROQ_MODEL,
    "groq/compound",
    "openai/gpt-oss-120b",
    "qwen/qwen3.8-27b",
    "groq/compound-mini",
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


async def _generate_content_with_fallback(contents, config):
    active_clients = clients
    if client is not None and (not isinstance(client, genai.Client) or not clients):
        active_clients = [client]

    last_exc = None
    if active_clients:
        for model in FALLBACK_MODELS:
            for idx, c in enumerate(active_clients):
                try:
                    return await c.aio.models.generate_content(
                        model=model,
                        contents=contents,
                        config=config,
                    )
                except (ClientError, APIError) as exc:
                    err_str = str(exc)
                    if (
                        "429" in err_str
                        or "RESOURCE_EXHAUSTED" in err_str
                        or "quota" in err_str.lower()
                    ):
                        logger.warning(
                            f"[Agent] Gemini Key #{idx + 1} model '{model}' hit rate limit/quota. Retrying fallback..."
                        )
                        last_exc = exc
                        continue
                    logger.error(
                        f"[Agent] Gemini Key #{idx + 1} model '{model}' error: {exc}"
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
                "\n1. You MUST inspect the repository with MCP tools before answering repository-specific questions."
                "\n2. Never answer repository questions from assumptions or general knowledge."
                "\n3. Never stop after discovering filenames. Filenames alone are not an analysis."
                "\n4. Use multiple MCP tool calls when necessary to understand the implementation."
                "\n5. Treat MCP results as the source of truth."
                "\n6. Never claim that you inspected a file unless the MCP tool actually returned its contents."
                "\n7. Never say you cannot access the repository when MCP tools are available."
                "\n8. FOLLOW-UP RE-EVALUATION RULE: If previous history mentions that a file (e.g. `assistantRoutes.js` or `assistantController.js`) could not be retrieved, NEVER repeat that limitation. In this turn, you MUST call `get_file_contents` to fetch and analyze the file now."
                "\n\n"
                "API ANALYSIS:"
                "\nWhen the user asks questions such as:"
                "\n- 'What APIs does this repo have?'"
                "\n- 'What APIs are there and what do they do?'"
                "\n- 'Explain the APIs in this project.'"
                "\n- 'What does this API do?'"
                "\n\nDO NOT simply list route files such as:"
                "\n- authRoutes.js"
                "\n- alertRoutes.js"
                "\n- machineRoutes.js"
                "\nThose filenames are only the starting point."
                "\n\nYou MUST perform the following investigation:"
                "\n1. Locate the backend/API entry point and route files."
                "\n2. Read the relevant route files using get_file_contents."
                "\n3. Identify the actual HTTP method and endpoint path for each route."
                "\n4. Identify the controller/handler called by each route."
                "\n5. Read the relevant controller/handler implementation."
                "\n6. If necessary, follow the controller into the service/model/database/external API code."
                "\n7. Determine what the endpoint actually does from the implementation."
                "\n8. Determine important request parameters, path parameters, query parameters, and request body fields when they are visible."
                "\n9. Determine the important response/result when it can be established from the code."
                "\n10. Group related endpoints by feature."
                "\n\nFor example, if MCP discovers:"
                "\nrouter.post('/predict', predictionController.predict)"
                "\nDO NOT answer only:"
                "\n'POST /predict exists.'"
                "\nInstead, inspect predictionController.predict and relevant downstream code and explain:"
                "\n- what prediction is being generated"
                "\n- what input it expects"
                "\n- what data/model/service it uses"
                "\n- what it returns"
                "\n- why this endpoint exists in the application"
                "\n\nAPI RESPONSE FORMAT:"
                "\nStart with a short overview of what the backend API layer is responsible for."
                "\nThen group endpoints by feature/module."
                "\nFor each important endpoint provide:"
                "\n- Method + path"
                "\n- Purpose"
                "\n- Request/input"
                "\n- What it does internally"
                "\n- Response/output when determinable"
                "\n- Relevant source file(s)"
                "\n\nDo not fabricate missing information. "
                "If the implementation does not reveal a detail, explicitly say that it could not be determined."
                "\n\n"
                "DEPTH RULE:"
                "\nFor API questions, continue calling MCP tools until you have enough evidence "
                "to explain the functionality rather than merely identify the endpoint. "
                "However, do not read the entire repository unnecessarily. "
                "Follow the implementation only as deeply as required to answer the user's question."
                "\n\n"
                "ARCHITECTURE ANALYSIS:"
                "\nFor questions such as 'Give me the high-level architecture':"
                "\n- Inspect the repository tree."
                "\n- Inspect README and package manifests."
                "\n- Inspect frontend and backend entry points."
                "\n- Inspect routes/controllers/services/models as needed."
                "\n- Identify database, authentication, external APIs, AI/ML services, "
                "and infrastructure only when supported by repository evidence."
                "\n- Explain how the major components communicate."
                "\n- Do not simply list technologies."
                "\n\n"
                "FEATURE ANALYSIS:"
                "\nFor questions about how a specific feature works, trace the implementation:"
                "\nroute → controller/handler → service → database/model/external API"
                "\nwhen those layers exist."
                "\n\n"
                "LIVE GITHUB QUESTIONS:"
                "\nFor questions about commits, branches, pull requests, issues, or other live GitHub data, "
                "use the appropriate MCP tool directly."
                "\n\n"
                "FINAL ANSWER:"
                "\nAfter gathering repository evidence, synthesize a clear developer-focused answer."
                "\nDo not expose internal reasoning."
                "\nDo not dump raw source code unless the user asks for it."
                "\nUse headings, bullets, and tables when they improve readability."
                "\nMention relevant repository file paths as evidence."
            )
            for turn_idx in range(15):
                logger.info("[Agent] LLM reasoning turn %d...", turn_idx + 1)
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
                            steps.append(f"Read GitHub via MCP: {fallback_tool}")
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

                    if not text:
                        raise HTTPException(
                            502, "The model returned no answer. Please try again."
                        )

                    logger.info("[Agent] LLM synthesis completed")
                    logger.info("[Agent] Final response generated")
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
                    logger.info("[Agent] MCP tool selected: %s", call.name)
                    result = await mcp.call(call.name, call.args or {})
                    logger.info(
                        "[Agent] MCP tool executed: %s, result received", call.name
                    )
                    steps.append(f"Read GitHub via MCP: {call.name}")
                    responses.append(
                        types.Part.from_function_response(
                            name=call.name, response=result
                        )
                    )
                contents.append(types.Content(role="tool", parts=responses))

    raise HTTPException(502, "Reasoning turn limit reached (15 turns max). Try asking about a specific subfolder or module.")


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
