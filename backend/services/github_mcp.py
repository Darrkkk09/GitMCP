"""Request-scoped client for the official GitHub MCP Server. No shared tokens."""
import asyncio
from contextlib import asynccontextmanager
from fastapi import HTTPException
from mcp import ClientSession
from mcp.client.streamable_http import streamablehttp_client
from services.github_api import GitHubAPI, parse_repo_identifier
import logging

logger = logging.getLogger(__name__)

MCP_URL = "https://api.githubcopilot.com/mcp/"
READ_TOOLS = {
    "get_file_contents", "list_commits", "get_commit", "list_branches",
    "list_issues", "issue_read", "list_pull_requests", "pull_request_read",
    "actions_list", "actions_get", "get_job_logs", "get_workflow_run",
    "list_workflow_runs", "list_workflow_jobs", "get_workflow_run_logs",
    "get_latest_release", "list_releases", "list_tags",
}


class RawResult:
    @classmethod
    def model_validate(cls, obj):
        return obj


class RepositoryMCP:
    def __init__(self, session, user_id, owner, repo, token):
        self.session, self.user_id = session, user_id
        self.owner, self.repo = owner, repo
        self._token = token
        self.tools = {}

    async def discover(self):
        cursor = None
        for _ in range(10):
            page = await self.session.list_tools(cursor=cursor)
            tools_list = getattr(page, "tools", []) or []
            for tool in tools_list:
                props = (getattr(tool, "inputSchema", {}) or {}).get("properties", {}) or {}
                annotations = getattr(tool, "annotations", None)
                is_readonly = getattr(annotations, "readOnlyHint", False) if annotations else False
                if (tool.name in READ_TOOLS and is_readonly
                        and "owner" in props and "repo" in props):
                    self.tools[tool.name] = tool
            cursor = getattr(page, "nextCursor", None)
            if not cursor:
                break
        if not self.tools:
            raise HTTPException(502, "GitHub MCP offered no supported read-only tools.")
        return list(self.tools.values())

    async def call(self, name, arguments):
        if name not in self.tools:
            return {"error": "Tool is not permitted"}
        if GitHubAPI(self.user_id).token() != self._token:
            raise HTTPException(401, "GitHub connection changed. Retry the request.")
        arguments = dict(arguments)
        for key, expected in (("owner", self.owner), ("repo", self.repo)):
            if key in arguments and str(arguments[key]).lower() != expected.lower():
                return {"error": "Tool calls are restricted to the selected repository"}
            arguments[key] = expected
        
        from mcp import types
        try:
            raw_res = await self.session.send_request(
                types.ClientRequest(
                    types.CallToolRequest(
                        params=types.CallToolRequestParams(name=name, arguments=arguments),
                    )
                ),
                RawResult,
            )
        except Exception as exc:
            try:
                result = await self.session.call_tool(name, arguments=arguments)
                if getattr(result, "isError", False):
                    return {"error": "GitHub MCP could not complete this read. Check repository access, scopes, or rate limits."}
                blocks = getattr(result, "content", []) or []
                text = "\n".join(block.text for block in blocks if getattr(block, "type", None) == "text" and hasattr(block, "text") and block.text)
                text = text.replace(self._token, "[REDACTED]")
                return {"content": text[:24000], "truncated": len(text) > 24000}
            except Exception as e:
                logger.error(f"[Agent] MCP call_tool failed for {name}: {e}")
                return {"error": f"GitHub MCP tool execution error: {e}"}

        if isinstance(raw_res, dict) and raw_res.get("isError"):
            return {"error": "GitHub MCP could not complete this read. Check repository access, scopes, or rate limits."}

        content_raw = (raw_res.get("content", []) if isinstance(raw_res, dict) else []) or []
        texts = []
        if isinstance(content_raw, list):
            for item in content_raw:
                if isinstance(item, dict):
                    if item.get("type") == "text" and "text" in item and item["text"] is not None:
                        texts.append(str(item["text"]))
                    elif item.get("type") in ("resource", "embedded_resource"):
                        res = item.get("resource")
                        if isinstance(res, dict):
                            if "text" in res and res["text"] is not None:
                                texts.append(str(res["text"]))
                            elif "blob" in res and res["blob"] is not None:
                                texts.append(str(res["blob"]))
                            elif "uri" in res and res["uri"] is not None:
                                texts.append(str(res["uri"]))
                            else:
                                texts.append(str(res))
                        elif isinstance(res, str):
                            texts.append(res)
                    elif "text" in item and item["text"] is not None:
                        texts.append(str(item["text"]))
                elif item is not None and hasattr(item, "text") and getattr(item, "text", None) is not None:
                    texts.append(str(getattr(item, "text", "")))
        elif isinstance(content_raw, str):
            texts.append(content_raw)

        text = "\n".join(t for t in texts if t)
        text = text.replace(self._token, "[REDACTED]")
        return {"content": text[:24000], "truncated": len(text) > 24000}


def _unwrap_exception(exc: BaseException) -> BaseException:
    while hasattr(exc, "exceptions") and getattr(exc, "exceptions"):
        exc = exc.exceptions[0]
    return exc


@asynccontextmanager
async def repository_mcp(user_id, owner, repo):
    token = GitHubAPI(user_id).token()
    last_exc = None
    connected = False
    for attempt in range(3):
        try:
            async with streamablehttp_client(MCP_URL, headers={
                "Authorization": "Bearer " + token,
                "X-MCP-Readonly": "true",
                "X-MCP-Toolsets": "repos,issues,pull_requests,actions",
            }, timeout=30, sse_read_timeout=90) as (read, write, _):
                async with ClientSession(read, write) as session:
                    await session.initialize()
                    try:
                        yield RepositoryMCP(session, user_id, owner, repo, token)
                    finally:
                        connected = True
                    return
        except HTTPException:
            raise
        except BaseException as exc:
            if connected:
                raise
            real_exc = _unwrap_exception(exc)
            if isinstance(real_exc, (HTTPException, GeneratorExit)):
                raise real_exc
            import httpx
            if isinstance(real_exc, httpx.HTTPStatusError):
                if real_exc.response.status_code in (401, 403):
                    logger.error(f"GitHub MCP auth failed for {owner}/{repo}: HTTP {real_exc.response.status_code}")
                    raise HTTPException(401, "GitHub Copilot MCP unauthorized. Please reconnect your GitHub account.") from None
            last_exc = real_exc
            logger.warning(f"GitHub MCP connection attempt {attempt + 1} failed for {owner}/{repo}: {real_exc}")
            if attempt < 2:
                await asyncio.sleep(0.5 * (attempt + 1))

    logger.error(f"GitHub MCP connection failed for {owner}/{repo}: {last_exc}")
    raise HTTPException(502, f"GitHub MCP is unavailable: {last_exc}") from None
