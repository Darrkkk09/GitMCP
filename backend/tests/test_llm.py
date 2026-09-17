import logging
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest
from unittest.mock import AsyncMock, Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import llm_service
from models.schemas import ChatMessage
from google.genai import types


class LLMTests(unittest.IsolatedAsyncioTestCase):
    async def test_mcp_passes_history_without_credentials_to_model(self):
        mcp = SimpleNamespace(_token="private-oauth-token", discover=AsyncMock(return_value=[SimpleNamespace(
            name="list_commits", description="List commits", inputSchema={"type": "object", "properties": {
                "owner": {"type": "string"}, "repo": {"type": "string"}}})]),
            call=AsyncMock(return_value={"content": "commit abc123"}))

        @asynccontextmanager
        async def connect(user_id, owner, repo):
            self.assertEqual((user_id, owner, repo), ("user-a", "a", "repo"))
            yield mcp

        model_content = types.Content(role="model", parts=[types.Part.from_function_call(
            name="list_commits", args={"owner": "a", "repo": "repo"})])
        responses = [SimpleNamespace(function_calls=[SimpleNamespace(name="list_commits", args={})],
                                    candidates=[SimpleNamespace(content=model_content)]),
                     SimpleNamespace(function_calls=[], text="commit abc123; private-oauth-token")]
        generate = AsyncMock(side_effect=responses)
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
        with patch.object(llm_service, "client", client), patch.object(llm_service, "repository_mcp", connect):
            result = await llm_service.answer_question_with_mcp("user-a", "a", "repo", "What changed?",
                history=[ChatMessage(role="user", content="Explain login")])
        self.assertNotIn(mcp._token, result["answer"])
        sent = str(generate.call_args_list)
        self.assertIn("Explain login", sent)
        self.assertNotIn(mcp._token, sent)
        mcp.call.assert_awaited_once_with("list_commits", {})

    async def test_mcp_fallback_tool_invocation_on_refusal(self):
        mcp = SimpleNamespace(_token="tok", tools={"get_file_contents": True}, discover=AsyncMock(return_value=[SimpleNamespace(
            name="get_file_contents", description="Get file contents", inputSchema={"type": "object", "properties": {
                "owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"}}})]),
            call=AsyncMock(return_value={"content": "README contents"}))

        @asynccontextmanager
        async def connect(user_id, owner, repo):
            yield mcp

        responses = [
            SimpleNamespace(function_calls=[], text="I cannot answer this question as I do not have access."),
            SimpleNamespace(function_calls=[], text="Here are the APIs in README contents.")
        ]
        generate = AsyncMock(side_effect=responses)
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))
        with patch.object(llm_service, "client", client), patch.object(llm_service, "repository_mcp", connect):
            result = await llm_service.answer_question_with_mcp("user-a", "a", "repo", "what APIs do this repo have?")
        self.assertIn("Here are the APIs", result["answer"])
        mcp.call.assert_awaited_once_with("get_file_contents", {"path": "README.md"})
