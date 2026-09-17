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
            for tool in page.tools:
                props = tool.inputSchema.get("properties", {})
                if (tool.name in READ_TOOLS and tool.annotations and tool.annotations.readOnlyHint
                        and "owner" in props and "repo" in props):
                    self.tools[tool.name] = tool
            cursor = page.nextCursor
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
        result = await self.session.call_tool(name, arguments=arguments)
        if result.isError:
            return {"error": "GitHub MCP could not complete this read. Check repository access, scopes, or rate limits."}
        text = "\n".join(block.text for block in result.content if block.type == "text")
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
