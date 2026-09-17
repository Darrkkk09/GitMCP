"""Exercise real MCP initialize/list/call messages over the SDK memory transport."""
import asyncio
from contextlib import asynccontextmanager
from pathlib import Path
import sys
import unittest
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from mcp.server.fastmcp import FastMCP
from mcp.shared.memory import create_connected_server_and_client_session
from mcp.types import ToolAnnotations
from services.github_mcp import RepositoryMCP, repository_mcp


class MCPTests(unittest.IsolatedAsyncioTestCase):
    async def test_actual_protocol_and_repository_scope(self):
        server = FastMCP("GiTMCP test server")
        calls = []

        @server.tool(annotations=ToolAnnotations(readOnlyHint=True))
        def list_commits(owner: str, repo: str) -> str:
            calls.append((owner, repo))
            return f"{owner}/{repo}: commit 123"

        @server.tool(annotations=ToolAnnotations(readOnlyHint=False))
        def delete_repository(owner: str, repo: str) -> str:
            raise AssertionError("Write tool must never execute")

        async with create_connected_server_and_client_session(server) as session:
            mcp = RepositoryMCP(session, "user-a", "a", "private", "token-a")
            tools = await mcp.discover()
            self.assertEqual([t.name for t in tools], ["list_commits"])
            with patch("services.github_mcp.GitHubAPI.token", return_value="token-a"):
                result = await mcp.call("list_commits", {})
                self.assertIn("a/private", result["content"])
                denied = await mcp.call("list_commits", {"owner": "b", "repo": "private"})
                self.assertIn("error", denied)
                self.assertIn("error", await mcp.call("delete_repository", {}))
            self.assertEqual(calls, [("a", "private")])
            with patch("services.github_mcp.GitHubAPI.token", return_value="changed"):
                with self.assertRaises(Exception) as caught:
                    await mcp.call("list_commits", {})
                self.assertEqual(caught.exception.status_code, 401)

    async def test_simultaneous_users_have_separate_http_headers_and_sessions(self):
        headers_seen, sessions = [], []

        @asynccontextmanager
        async def transport(url, headers, **kwargs):
            headers_seen.append(dict(headers))
            yield object(), object(), lambda: "session"

        @asynccontextmanager
        async def client(read, write):
            session = AsyncMock()
            sessions.append(session)
            yield session

        async def run(user):
            async with repository_mcp(user, user, "repo") as mcp:
                await asyncio.sleep(0)
                self.assertEqual(mcp.user_id, user)

        with patch("services.github_mcp.GitHubAPI.token", autospec=True,
                   side_effect=lambda api: "token-" + api.user_id), \
             patch("services.github_mcp.streamablehttp_client", transport), \
             patch("services.github_mcp.ClientSession", client):
            await asyncio.gather(run("a"), run("b"))
        self.assertEqual({h["Authorization"] for h in headers_seen}, {"Bearer token-a", "Bearer token-b"})
        self.assertTrue(all(h["X-MCP-Readonly"] == "true" for h in headers_seen))
        self.assertIsNot(sessions[0], sessions[1])


if __name__ == "__main__":
    unittest.main()
