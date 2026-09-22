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

    async def test_api_analysis_guard_forces_reading_route_contents(self):
        # Tool call 1 returns directory listing with route files
        call1 = SimpleNamespace(name="get_file_contents", args={"path": "backend/app/routes"})
        res1 = SimpleNamespace(function_calls=[call1], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "backend/app/routes"})]))])
        
        # Response 2 attempts synthesis without reading route file content
        res2 = SimpleNamespace(function_calls=[], text="There are routes: agent.py, cart.py", candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_text(text="There are routes: agent.py, cart.py")]))])

        # Response 3 calls get_file_contents for agent.py after guard triggers
        call3 = SimpleNamespace(name="get_file_contents", args={"path": "backend/app/routes/agent.py"})
        res3 = SimpleNamespace(function_calls=[call3], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "backend/app/routes/agent.py"})]))])

        # Final response 4 synthesizes endpoint details
        res4 = SimpleNamespace(function_calls=[], text="POST /agent/query - Purpose: processes agent queries", candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_text(text="POST /agent/query - Purpose: processes agent queries")]))])

        mcp_call_results = {
            "backend/app/routes": {"content": "agent.py"},
            "backend/app/routes/agent.py": {"content": "@router.post('/query')\ndef agent_query(): pass"},
        }
        async def mock_mcp_call(name, args):
            path = args.get("path", "")
            return mcp_call_results.get(path, {"content": "ok"})

        mcp = SimpleNamespace(_token="tok", tools={"get_file_contents": True}, discover=AsyncMock(return_value=[SimpleNamespace(
            name="get_file_contents", description="Get file contents", inputSchema={"type": "object", "properties": {
                "owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"}}})]),
            call=AsyncMock(side_effect=mock_mcp_call))

        @asynccontextmanager
        async def connect(user_id, owner, repo):
            yield mcp

        generate = AsyncMock(side_effect=[res1, res2, res3, res4])
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))

        with patch.object(llm_service, "client", client), patch.object(llm_service, "repository_mcp", connect):
            result = await llm_service.answer_question_with_mcp("user-a", "a", "repo", "What APIs does this repo have?")

        self.assertIn("POST /agent/query", result["answer"])
        self.assertIn("Directory discovered: backend/app/routes/", result["steps"])
        self.assertIn("Source retrieved: backend/app/routes/agent.py", result["steps"])
        self.assertIn("Analyzing endpoint behavior", result["steps"])

    async def test_system_instructions_contain_required_rules(self):
        mcp = SimpleNamespace(_token="tok", tools={"get_file_contents": True}, discover=AsyncMock(return_value=[SimpleNamespace(
            name="get_file_contents", description="Get file contents", inputSchema={"type": "object", "properties": {
                "owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"}}})]),
            call=AsyncMock(return_value={"content": "file content"}))

        @asynccontextmanager
        async def connect(user_id, owner, repo):
            yield mcp

        response = SimpleNamespace(function_calls=[], text="Repository explanation")
        generate = AsyncMock(return_value=response)
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))

        with patch.object(llm_service, "client", client), patch.object(llm_service, "repository_mcp", connect):
            await llm_service.answer_question_with_mcp("user-a", "a", "repo", "Explain structure")

        config = generate.call_args_list[0].kwargs["config"]
        sys_inst = config.system_instruction
        self.assertIn("Filenames are discovery evidence only, not implementation evidence. Never answer an API-analysis question using route filenames alone. Before describing API behavior, retrieve and inspect the source code of the relevant route files using MCP.", sys_inst)
        self.assertIn("Previous assistant responses are not repository evidence. Always perform fresh MCP inspection when the current question requires source-code information.", sys_inst)

    async def test_mcp_blocks_undiscovered_guessed_paths(self):
        async def mock_mcp_call(name, args):
            if args.get("path") == "":
                return {"content": '{"type": "dir", "name": "backend"}'}
            return {"content": "file content"}

        mcp = SimpleNamespace(_token="tok", tools={"get_file_contents": True}, discover=AsyncMock(return_value=[SimpleNamespace(
            name="get_file_contents", description="Get file contents", inputSchema={"type": "object", "properties": {
                "owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"}}})]),
            call=AsyncMock(side_effect=mock_mcp_call))

        @asynccontextmanager
        async def connect(user_id, owner, repo):
            yield mcp

        # First call: discover root
        call0 = SimpleNamespace(name="get_file_contents", args={"path": ""})
        res0 = SimpleNamespace(function_calls=[call0], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": ""})]))])

        # Second call: guess file
        call1 = SimpleNamespace(name="get_file_contents", args={"path": "backend/routes/guessedRoutes.js"})
        res1 = SimpleNamespace(function_calls=[call1], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "backend/routes/guessedRoutes.js"})]))])
        
        # Third call: finish
        res2 = SimpleNamespace(function_calls=[], text="Finished", candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_text(text="Finished")]))])
        
        generate = AsyncMock(side_effect=[res0, res1, res2])
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))

        with patch.object(llm_service, "client", client), patch.object(llm_service, "repository_mcp", connect):
            result = await llm_service.answer_question_with_mcp("user-a", "a", "repo", "What are the routes?")

        # MCP should NOT have been called with the guessed path, only the root discovery path
        mcp.call.assert_called_once_with("get_file_contents", {"path": ""})
        self.assertIn("Blocked unverified path: backend/routes/guessedRoutes.js", result["steps"])

    async def test_mcp_forces_discovery_before_file_retrieval(self):
        mcp = SimpleNamespace(_token="tok", tools={"get_file_contents": True}, discover=AsyncMock(return_value=[SimpleNamespace(
            name="get_file_contents", description="Get file contents", inputSchema={"type": "object", "properties": {
                "owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"}}})]),
            call=AsyncMock(return_value={"content": "file content"}))

        @asynccontextmanager
        async def connect(user_id, owner, repo):
            yield mcp

        # Call get_file_contents for a file without discovering first
        call1 = SimpleNamespace(name="get_file_contents", args={"path": "backend/routes/api.py"})
        res1 = SimpleNamespace(function_calls=[call1], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "backend/routes/api.py"})]))])
        
        # Second call we provide final response to end the loop
        res2 = SimpleNamespace(function_calls=[], text="Finished", candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_text(text="Finished")]))])
        
        generate = AsyncMock(side_effect=[res1, res2])
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))

        with patch.object(llm_service, "client", client), patch.object(llm_service, "repository_mcp", connect):
            result = await llm_service.answer_question_with_mcp("user-a", "a", "repo", "What APIs does this repo have?")

        # MCP should NOT have been called with the file path
        mcp.call.assert_not_called()
        self.assertIn("Discovery required before file retrieval", result["steps"])

    async def test_source_code_analysis(self):
        mcp_call_results = {
            "": '[{"type": "file", "name": "users.py", "path": "routes/users.py"}]',
            "routes/users.py": "@router.get('/users')\ndef get_users(): return user_service.get_users()",
        }
        async def mock_call(name, args):
            return {"content": mcp_call_results.get(args.get("path", ""), "ok")}

        mcp = SimpleNamespace(_token="tok", tools={"get_file_contents": True}, discover=AsyncMock(return_value=[SimpleNamespace(
            name="get_file_contents", description="Get file contents", inputSchema={"type": "object", "properties": {
                "owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"}}})]),
            call=AsyncMock(side_effect=mock_call))

        @asynccontextmanager
        async def connect(user_id, owner, repo):
            yield mcp

        call0 = SimpleNamespace(name="get_file_contents", args={"path": ""})
        res0 = SimpleNamespace(function_calls=[call0], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": ""})]))])

        call1 = SimpleNamespace(name="get_file_contents", args={"path": "routes/users.py"})
        res1 = SimpleNamespace(function_calls=[call1], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "routes/users.py"})]))])

        res2 = SimpleNamespace(function_calls=[], text="GET /users fetches user list from user_service", candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_text(text="GET /users fetches user list from user_service")]))])

        generate = AsyncMock(side_effect=[res0, res1, res2])
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))

        with patch.object(llm_service, "client", client), patch.object(llm_service, "repository_mcp", connect):
            result = await llm_service.answer_question_with_mcp("user-a", "a", "repo", "Explain GET /users")

        self.assertIn("Source retrieved: routes/users.py", result["steps"])
        self.assertIn("Analyzing source: routes/users.py", result["steps"])
        self.assertIn("GET /users fetches user list", result["answer"])

    async def test_filename_only_regression(self):
        mcp_call_results = {
            "routes": '[{"type": "file", "name": "users.py", "path": "routes/users.py"}]',
            "routes/users.py": "@router.get('/users')\ndef get_users(): return []",
        }
        async def mock_call(name, args):
            return {"content": mcp_call_results.get(args.get("path", ""), "ok")}

        mcp = SimpleNamespace(_token="tok", tools={"get_file_contents": True}, discover=AsyncMock(return_value=[SimpleNamespace(
            name="get_file_contents", description="Get file contents", inputSchema={"type": "object", "properties": {
                "owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"}}})]),
            call=AsyncMock(side_effect=mock_call))

        @asynccontextmanager
        async def connect(user_id, owner, repo):
            yield mcp

        call1 = SimpleNamespace(name="get_file_contents", args={"path": "routes"})
        res1 = SimpleNamespace(function_calls=[call1], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "routes"})]))])

        # LLM returns filename only
        res2 = SimpleNamespace(function_calls=[], text="routes/users.py exists", candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_text(text="routes/users.py exists")]))])

        # Guard triggers and forces get_file_contents
        call3 = SimpleNamespace(name="get_file_contents", args={"path": "routes/users.py"})
        res3 = SimpleNamespace(function_calls=[call3], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "routes/users.py"})]))])

        res4 = SimpleNamespace(function_calls=[], text="GET /users returns empty list", candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_text(text="GET /users returns empty list")]))])

        generate = AsyncMock(side_effect=[res1, res2, res3, res4])
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))

        with patch.object(llm_service, "client", client), patch.object(llm_service, "repository_mcp", connect):
            result = await llm_service.answer_question_with_mcp("user-a", "a", "repo", "What APIs does this repo have?")

        self.assertIn("GET /users returns empty list", result["answer"])
        self.assertIn("Source retrieved: routes/users.py", result["steps"])

    async def test_cross_file_dependency_analysis(self):
        mcp_call_results = {
            "": '[{"type": "file", "name": "users.py", "path": "routes/users.py"}]',
            "routes/users.py": "from services.user_service import get_users\n@router.get('/users')\ndef route(): return get_users()",
            "services/user_service.py": "from repositories.user_repository import find_users\ndef get_users(): return find_users()",
            "repositories/user_repository.py": "def find_users(): return db.query(User).all()",
        }
        async def mock_call(name, args):
            return {"content": mcp_call_results.get(args.get("path", ""), "ok")}

        mcp = SimpleNamespace(_token="tok", tools={"get_file_contents": True}, discover=AsyncMock(return_value=[SimpleNamespace(
            name="get_file_contents", description="Get file contents", inputSchema={"type": "object", "properties": {
                "owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"}}})]),
            call=AsyncMock(side_effect=mock_call))

        @asynccontextmanager
        async def connect(user_id, owner, repo):
            yield mcp

        # Step 1: Discover root
        call0 = SimpleNamespace(name="get_file_contents", args={"path": ""})
        res0 = SimpleNamespace(function_calls=[call0], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": ""})]))])

        # Step 2: Fetch route
        call1 = SimpleNamespace(name="get_file_contents", args={"path": "routes/users.py"})
        res1 = SimpleNamespace(function_calls=[call1], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "routes/users.py"})]))])

        # Step 3: Fetch service dependency
        call2 = SimpleNamespace(name="get_file_contents", args={"path": "services/user_service.py"})
        res2 = SimpleNamespace(function_calls=[call2], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "services/user_service.py"})]))])

        # Step 4: Fetch repository dependency
        call3 = SimpleNamespace(name="get_file_contents", args={"path": "repositories/user_repository.py"})
        res3 = SimpleNamespace(function_calls=[call3], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "repositories/user_repository.py"})]))])

        # Step 5: Final synthesis
        res4 = SimpleNamespace(function_calls=[], text="GET /users flow: route -> user_service -> user_repository (queries DB)", candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_text(text="GET /users flow: route -> user_service -> user_repository (queries DB)")]))])

        generate = AsyncMock(side_effect=[res0, res1, res2, res3, res4])
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))

        with patch.object(llm_service, "client", client), patch.object(llm_service, "repository_mcp", connect):
            result = await llm_service.answer_question_with_mcp("user-a", "a", "repo", "Explain GET /users flow")

        self.assertIn("Dependency source retrieved: services/user_service.py", result["steps"])
        self.assertIn("Cross-file analysis: services/user_service.py", result["steps"])
        self.assertIn("Dependency source retrieved: repositories/user_repository.py", result["steps"])
        self.assertIn("Cross-file analysis: repositories/user_repository.py", result["steps"])

    async def test_api_implementation_analysis(self):
        mcp_call_results = {
            "routes": '[{"type": "file", "name": "users.py", "path": "routes/users.py"}]',
            "routes/users.py": "@router.post('/login')\ndef login(req: LoginReq): return auth_service.login(req)",
        }
        async def mock_call(name, args):
            return {"content": mcp_call_results.get(args.get("path", ""), "ok")}

        mcp = SimpleNamespace(_token="tok", tools={"get_file_contents": True}, discover=AsyncMock(return_value=[SimpleNamespace(
            name="get_file_contents", description="Get file contents", inputSchema={"type": "object", "properties": {
                "owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"}}})]),
            call=AsyncMock(side_effect=mock_call))

        @asynccontextmanager
        async def connect(user_id, owner, repo):
            yield mcp

        call1 = SimpleNamespace(name="get_file_contents", args={"path": "routes"})
        res1 = SimpleNamespace(function_calls=[call1], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "routes"})]))])

        call2 = SimpleNamespace(name="get_file_contents", args={"path": "routes/users.py"})
        res2 = SimpleNamespace(function_calls=[call2], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "routes/users.py"})]))])

        res3 = SimpleNamespace(function_calls=[], text="POST /login - Method: POST, Path: /login, Request: LoginReq, Processing: calls auth_service.login, Response: Token", candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_text(text="POST /login - Method: POST, Path: /login, Request: LoginReq, Processing: calls auth_service.login, Response: Token")]))])

        generate = AsyncMock(side_effect=[res1, res2, res3])
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))

        with patch.object(llm_service, "client", client), patch.object(llm_service, "repository_mcp", connect):
            result = await llm_service.answer_question_with_mcp("user-a", "a", "repo", "What APIs does this repository have? Explain what each API does, including method, path, request, processing, dependencies, and response.")

        self.assertIn("Repository discovered", result["steps"])
        self.assertIn("Source retrieved: routes/users.py", result["steps"])
        self.assertIn("Analyzing source: routes/users.py", result["steps"])
        self.assertIn("Analyzing endpoint behavior", result["steps"])
        self.assertIn("Final code analysis completed", result["steps"])
        self.assertIn("POST /login", result["answer"])

    async def test_batch_mcp_retrieval_single_turn(self):
        mcp_call_results = {
            "": '[{"type": "file", "name": "a.py", "path": "routes/a.py"}, {"type": "file", "name": "b.py", "path": "routes/b.py"}]',
            "routes/a.py": "def a(): pass",
            "routes/b.py": "def b(): pass",
        }
        async def mock_call(name, args):
            return {"content": mcp_call_results.get(args.get("path", ""), "ok")}

        mcp = SimpleNamespace(_token="tok", tools={"get_file_contents": True}, discover=AsyncMock(return_value=[SimpleNamespace(
            name="get_file_contents", description="Get file contents", inputSchema={"type": "object", "properties": {
                "owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"}}})]),
            call=AsyncMock(side_effect=mock_call))

        @asynccontextmanager
        async def connect(user_id, owner, repo):
            yield mcp

        # Call 1: Discover root
        c0 = SimpleNamespace(name="get_file_contents", args={"path": ""})
        r0 = SimpleNamespace(function_calls=[c0], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": ""})]))])

        # Call 2: Batched parallel calls for both a.py and b.py in ONE turn
        c1 = SimpleNamespace(name="get_file_contents", args={"path": "routes/a.py"})
        c2 = SimpleNamespace(name="get_file_contents", args={"path": "routes/b.py"})
        r1 = SimpleNamespace(function_calls=[c1, c2], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[
            types.Part.from_function_call(name="get_file_contents", args={"path": "routes/a.py"}),
            types.Part.from_function_call(name="get_file_contents", args={"path": "routes/b.py"})
        ]))])

        # Call 3: Final synthesis
        r2 = SimpleNamespace(function_calls=[], text="Analyzed both a.py and b.py in batch", candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_text(text="Analyzed both a.py and b.py in batch")]))])

        generate = AsyncMock(side_effect=[r0, r1, r2])
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))

        with patch.object(llm_service, "client", client), patch.object(llm_service, "repository_mcp", connect):
            result = await llm_service.answer_question_with_mcp("user-a", "a", "repo", "Analyze routes")

        self.assertEqual(generate.call_count, 3)
        self.assertIn("Source retrieved: routes/a.py", result["steps"])
        self.assertIn("Source retrieved: routes/b.py", result["steps"])

    async def test_max_agent_turns_enforced(self):
        mcp = SimpleNamespace(_token="tok", tools={"get_file_contents": True}, discover=AsyncMock(return_value=[SimpleNamespace(
            name="get_file_contents", description="Get file contents", inputSchema={"type": "object", "properties": {
                "owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"}}})]),
            call=AsyncMock(return_value={"content": "ok"}))

        @asynccontextmanager
        async def connect(user_id, owner, repo):
            yield mcp

        # Infinite tool call loop
        c0 = SimpleNamespace(name="get_file_contents", args={"path": ""})
        r0 = SimpleNamespace(function_calls=[c0], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": ""})]))])
        generate = AsyncMock(return_value=r0)
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))

        with patch.object(llm_service, "client", client), patch.object(llm_service, "repository_mcp", connect):
            with self.assertRaises(llm_service.HTTPException) as ctx:
                await llm_service.answer_question_with_mcp("user-a", "a", "repo", "Infinite query")

        self.assertEqual(ctx.exception.status_code, 502)
        self.assertIn("6 turns max", ctx.exception.detail)
        self.assertEqual(generate.call_count, llm_service.MAX_AGENT_TURNS)

    def test_context_budget_per_file_limit(self):
        budget = llm_service.ContextBudget(max_source_per_file=100)
        large_content = "x" * 250
        allowed, status, processed = budget.can_add_source("large.py", large_content)
        self.assertTrue(allowed)
        self.assertEqual(status, "truncated")
        self.assertIn("[Source truncated by context budget.", processed)

    def test_context_budget_total_source_limit(self):
        budget = llm_service.ContextBudget(max_total_source=100)
        allowed1, status1, proc1 = budget.add_source("file1.py", "a" * 80)
        self.assertTrue(allowed1)
        allowed2, status2, proc2 = budget.add_source("file2.py", "b" * 80)
        self.assertFalse(allowed2)
        self.assertEqual(status2, "budget_exceeded")

    def test_context_budget_duplicate_source(self):
        budget = llm_service.ContextBudget()
        allowed1, status1, _ = budget.add_source("file1.py", "content 1")
        self.assertTrue(allowed1)
        allowed2, status2, _ = budget.add_source("file1.py", "content 1")
        self.assertFalse(allowed2)
        self.assertEqual(status2, "duplicate")

    def test_estimate_tokens(self):
        text = "a" * 400
        tokens = llm_service.estimate_tokens(text)
        self.assertEqual(tokens, 100)

    async def test_duplicate_source_in_query_flow(self):
        mcp_call_results = {
            "": '[{"type": "file", "name": "a.py", "path": "routes/a.py"}]',
            "routes/a.py": "def a(): pass",
        }
        async def mock_call(name, args):
            return {"content": mcp_call_results.get(args.get("path", ""), "ok")}

        mcp = SimpleNamespace(_token="tok", tools={"get_file_contents": True}, discover=AsyncMock(return_value=[SimpleNamespace(
            name="get_file_contents", description="Get file contents", inputSchema={"type": "object", "properties": {
                "owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"}}})]),
            call=AsyncMock(side_effect=mock_call))

        @asynccontextmanager
        async def connect(user_id, owner, repo):
            yield mcp

        c0 = SimpleNamespace(name="get_file_contents", args={"path": ""})
        r0 = SimpleNamespace(function_calls=[c0], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": ""})]))])

        c1 = SimpleNamespace(name="get_file_contents", args={"path": "routes/a.py"})
        r1 = SimpleNamespace(function_calls=[c1], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "routes/a.py"})]))])

        # Second call asking for same file
        c2 = SimpleNamespace(name="get_file_contents", args={"path": "routes/a.py"})
        r2 = SimpleNamespace(function_calls=[c2], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "routes/a.py"})]))])

        r3 = SimpleNamespace(function_calls=[], text="Done", candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_text(text="Done")]))])

        generate = AsyncMock(side_effect=[r0, r1, r2, r3])
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))

        with patch.object(llm_service, "client", client), patch.object(llm_service, "repository_mcp", connect):
            result = await llm_service.answer_question_with_mcp("user-a", "a", "repo", "Analyze routes")

        self.assertIn("[Context] Duplicate skipped: routes/a.py", result["steps"])

    async def test_regression_test_a_directory_only_result_does_not_start_analysis(self):
        # Test A: MCP returns routes/ directory listing with no retrieved source files.
        # Analysis must NOT start (steps must NOT contain 'Analyzing endpoint behavior').
        mcp_call_results = {
            "routes": '[{"type": "file", "name": "app.js", "path": "routes/app.js"}]',
        }
        async def mock_call(name, args):
            return {"content": mcp_call_results.get(args.get("path", ""), "ok")}

        mcp = SimpleNamespace(_token="tok", tools={"get_file_contents": True}, discover=AsyncMock(return_value=[SimpleNamespace(
            name="get_file_contents", description="Get file contents", inputSchema={"type": "object", "properties": {
                "owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"}}})]),
            call=AsyncMock(side_effect=mock_call))

        @asynccontextmanager
        async def connect(user_id, owner, repo):
            yield mcp

        c0 = SimpleNamespace(name="get_file_contents", args={"path": "routes"})
        r0 = SimpleNamespace(function_calls=[c0], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "routes"})]))])
        # Model attempts to synthesize prematurely after seeing directory listing
        r1 = SimpleNamespace(function_calls=[], text="", candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_text(text="")]))])

        generate = AsyncMock(side_effect=[r0, r1, r1, r1, r1])
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))

        with patch.object(llm_service, "client", client), patch.object(llm_service, "repository_mcp", connect):
            result = await llm_service.answer_question_with_mcp("user-a", "a", "repo", "What APIs does this repository have?")

        self.assertNotIn("Analyzing endpoint behavior", result["steps"])
        self.assertNotIn("Final code analysis completed", result["steps"])
        self.assertIn("I could not retrieve the repository source files needed to analyze the APIs.", result["answer"])

    async def test_regression_test_b_directory_followed_by_actual_files(self):
        # Test B: Directory -> actual files -> source retrieval -> analysis
        mcp_call_results = {
            "routes": '[{"type": "file", "name": "api.py", "path": "routes/api.py"}, {"type": "file", "name": "auth.py", "path": "routes/auth.py"}]',
            "routes/api.py": "@router.get('/api')\ndef api(): pass",
            "routes/auth.py": "@router.post('/login')\ndef login(): pass",
        }
        async def mock_call(name, args):
            return {"content": mcp_call_results.get(args.get("path", ""), "ok")}

        mcp = SimpleNamespace(_token="tok", tools={"get_file_contents": True}, discover=AsyncMock(return_value=[SimpleNamespace(
            name="get_file_contents", description="Get file contents", inputSchema={"type": "object", "properties": {
                "owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"}}})]),
            call=AsyncMock(side_effect=mock_call))

        @asynccontextmanager
        async def connect(user_id, owner, repo):
            yield mcp

        c0 = SimpleNamespace(name="get_file_contents", args={"path": "routes"})
        r0 = SimpleNamespace(function_calls=[c0], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "routes"})]))])

        c1 = SimpleNamespace(name="get_file_contents", args={"path": "routes/api.py"})
        c2 = SimpleNamespace(name="get_file_contents", args={"path": "routes/auth.py"})
        r1 = SimpleNamespace(function_calls=[c1, c2], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[
            types.Part.from_function_call(name="get_file_contents", args={"path": "routes/api.py"}),
            types.Part.from_function_call(name="get_file_contents", args={"path": "routes/auth.py"}),
        ]))])

        r2 = SimpleNamespace(function_calls=[], text="GET /api and POST /login APIs defined", candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_text(text="GET /api and POST /login APIs defined")]))])

        generate = AsyncMock(side_effect=[r0, r1, r2])
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))

        with patch.object(llm_service, "client", client), patch.object(llm_service, "repository_mcp", connect):
            result = await llm_service.answer_question_with_mcp("user-a", "a", "repo", "What APIs does this repository have?")

        self.assertIn("Route files discovered: routes/api.py, routes/auth.py", result["steps"])
        self.assertIn("Source retrieved: routes/api.py", result["steps"])
        self.assertIn("Source retrieved: routes/auth.py", result["steps"])
        self.assertIn("Analyzing endpoint behavior", result["steps"])
        self.assertIn("Final code analysis completed", result["steps"])

    async def test_regression_test_c_guessed_path_blocks_analysis(self):
        # Test C: Guessed path requested by LLM when MCP never returned it.
        # Must emit 'Blocked unverified path: app.js' and MUST NOT start analysis.
        mcp = SimpleNamespace(_token="tok", tools={"get_file_contents": True}, discover=AsyncMock(return_value=[SimpleNamespace(
            name="get_file_contents", description="Get file contents", inputSchema={"type": "object", "properties": {
                "owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"}}})]),
            call=AsyncMock(return_value={"content": "file content"}))

        @asynccontextmanager
        async def connect(user_id, owner, repo):
            yield mcp

        c0 = SimpleNamespace(name="get_file_contents", args={"path": "routes"})
        r0 = SimpleNamespace(function_calls=[c0], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "routes"})]))])

        # Guessed unverified path app.js
        c1 = SimpleNamespace(name="get_file_contents", args={"path": "app.js"})
        r1 = SimpleNamespace(function_calls=[c1], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "app.js"})]))])

        r2 = SimpleNamespace(function_calls=[], text="Failed", candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_text(text="Failed")]))])

        generate = AsyncMock(side_effect=[r0, r1, r2, r2])
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))

        with patch.object(llm_service, "client", client), patch.object(llm_service, "repository_mcp", connect):
            result = await llm_service.answer_question_with_mcp("user-a", "a", "repo", "What APIs does this repository have?")

        self.assertIn("Blocked unverified path: app.js", result["steps"])
        self.assertNotIn("Analyzing endpoint behavior", result["steps"])
        self.assertNotIn("Final code analysis completed", result["steps"])

    async def test_regression_test_d_actual_source_retrieval_allows_analysis(self):
        # Test D: When at least one verified route source is retrieved, analysis is allowed.
        mcp_call_results = {
            "routes": '[{"type": "file", "name": "payment.js", "path": "routes/payment.js"}]',
            "routes/payment.js": "router.post('/pay', (req, res) => res.json({ status: 'ok' }));",
        }
        async def mock_call(name, args):
            return {"content": mcp_call_results.get(args.get("path", ""), "ok")}

        mcp = SimpleNamespace(_token="tok", tools={"get_file_contents": True}, discover=AsyncMock(return_value=[SimpleNamespace(
            name="get_file_contents", description="Get file contents", inputSchema={"type": "object", "properties": {
                "owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"}}})]),
            call=AsyncMock(side_effect=mock_call))

        @asynccontextmanager
        async def connect(user_id, owner, repo):
            yield mcp

        c0 = SimpleNamespace(name="get_file_contents", args={"path": "routes"})
        r0 = SimpleNamespace(function_calls=[c0], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "routes"})]))])

        c1 = SimpleNamespace(name="get_file_contents", args={"path": "routes/payment.js"})
        r1 = SimpleNamespace(function_calls=[c1], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "routes/payment.js"})]))])

        r2 = SimpleNamespace(function_calls=[], text="POST /pay route analyzed", candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_text(text="POST /pay route analyzed")]))])

        generate = AsyncMock(side_effect=[r0, r1, r2])
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))

        with patch.object(llm_service, "client", client), patch.object(llm_service, "repository_mcp", connect):
            result = await llm_service.answer_question_with_mcp("user-a", "a", "repo", "What APIs does this repository have?")

        self.assertIn("Source retrieved: routes/payment.js", result["steps"])
        self.assertIn("Analyzing endpoint behavior", result["steps"])
        self.assertIn("Final code analysis completed", result["steps"])

    async def test_regression_test_e_fallback_message_accuracy(self):
        # Test E: Verify fallback message accuracy.
        # "Code retrieval completed..." is produced only when source retrieval succeeded but text generation failed.
        # "I could not retrieve..." is produced when no source was retrieved.
        mcp_call_results = {
            "routes": '[{"type": "file", "name": "api.py", "path": "routes/api.py"}]',
            "routes/api.py": "def api(): pass",
        }
        async def mock_call(name, args):
            return {"content": mcp_call_results.get(args.get("path", ""), "ok")}

        mcp = SimpleNamespace(_token="tok", tools={"get_file_contents": True}, discover=AsyncMock(return_value=[SimpleNamespace(
            name="get_file_contents", description="Get file contents", inputSchema={"type": "object", "properties": {
                "owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"}}})]),
            call=AsyncMock(side_effect=mock_call))

        @asynccontextmanager
        async def connect(user_id, owner, repo):
            yield mcp

        c0 = SimpleNamespace(name="get_file_contents", args={"path": "routes"})
        r0 = SimpleNamespace(function_calls=[c0], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "routes"})]))])

        c1 = SimpleNamespace(name="get_file_contents", args={"path": "routes/api.py"})
        r1 = SimpleNamespace(function_calls=[c1], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "routes/api.py"})]))])

        # Model returns empty text after retrieving source
        r2 = SimpleNamespace(function_calls=[], text="", candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_text(text="")]))])

        generate = AsyncMock(side_effect=[r0, r1, r2])
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))

        with patch.object(llm_service, "client", client), patch.object(llm_service, "repository_mcp", connect):
            result = await llm_service.answer_question_with_mcp("user-a", "a", "repo", "What APIs does this repository have?")

        self.assertIn("Code retrieval completed, but final source-code analysis failed. Please ask a more focused question.", result["answer"])

    async def test_gemini_3_6_flash_normalization(self):
        # 1. gemini-3.6-flash fallback with a conversation containing tool-role messages
        contents_with_tool = [
            types.Content(role="user", parts=[types.Part.from_text(text="Find APIs")]),
            types.Content(role="tool", parts=[
                types.Part.from_function_response(name="get_file_contents", response={"content": "def api(): pass"})
            ])
        ]
        norm = llm_service.normalize_contents_for_model(contents_with_tool, "gemini-3.6-flash")
        for item in norm:
            self.assertNotEqual(getattr(item, "role", None), "tool")
        
        # Verify tool output preserved in user content
        tool_text = "".join(p.text for c in norm for p in c.parts if hasattr(p, "text"))
        self.assertIn("MCP Tool 'get_file_contents' result:", tool_text)
        self.assertIn("def api(): pass", tool_text)

    async def test_dependency_retrieval_and_analysis(self):
        # 5. Actual route source retrieved -> Dependency retrieval works -> Final analysis allowed
        mcp_call_results = {
            "routes": '[{"type": "file", "name": "routes.py", "path": "routes/routes.py"}]',
            "routes/routes.py": "from services.user import get_user\n@router.get('/user')\ndef user(): return get_user()",
            "services/user.py": "def get_user(): return {'id': 1}",
        }
        async def mock_call(name, args):
            p = args.get("path", "")
            return {"content": mcp_call_results.get(p, "ok")}

        mcp = SimpleNamespace(_token="tok", tools={"get_file_contents": True}, discover=AsyncMock(return_value=[SimpleNamespace(
            name="get_file_contents", description="Get file contents", inputSchema={"type": "object", "properties": {
                "owner": {"type": "string"}, "repo": {"type": "string"}, "path": {"type": "string"}}})]),
            call=AsyncMock(side_effect=mock_call))

        @asynccontextmanager
        async def connect(user_id, owner, repo):
            yield mcp

        c0 = SimpleNamespace(name="get_file_contents", args={"path": "routes"})
        r0 = SimpleNamespace(function_calls=[c0], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "routes"})]))])

        c1 = SimpleNamespace(name="get_file_contents", args={"path": "routes/routes.py"})
        r1 = SimpleNamespace(function_calls=[c1], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "routes/routes.py"})]))])

        c2 = SimpleNamespace(name="get_file_contents", args={"path": "services/user.py"})
        r2 = SimpleNamespace(function_calls=[c2], candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_function_call(name="get_file_contents", args={"path": "services/user.py"})]))])

        r3 = SimpleNamespace(function_calls=[], text="Analyzed GET /user and services/user.py dependency", candidates=[SimpleNamespace(content=types.Content(role="model", parts=[types.Part.from_text(text="Analyzed GET /user and services/user.py dependency")]))])

        generate = AsyncMock(side_effect=[r0, r1, r2, r3])
        client = SimpleNamespace(aio=SimpleNamespace(models=SimpleNamespace(generate_content=generate)))

        with patch.object(llm_service, "client", client), patch.object(llm_service, "repository_mcp", connect):
            result = await llm_service.answer_question_with_mcp("user-a", "a", "repo", "What APIs does this repository have?")

        self.assertIn("Source retrieved: routes/routes.py", result["steps"])
        self.assertIn("Dependency source retrieved: services/user.py", result["steps"])
        self.assertIn("Final code analysis completed", result["steps"])

    async def test_budget_limits_enforced_after_normalization(self):
        # 6. Context budget limits remain enforced after fallback normalization
        large_text = "x" * 160_000
        contents = [
            types.Content(role="user", parts=[types.Part.from_text(text="Start")]),
            types.Content(role="tool", parts=[types.Part.from_function_response(name="tool", response={"content": large_text})]),
        ]
        norm = llm_service.normalize_contents_for_model(contents, "gemini-3.6-flash")
        compacted_chars = llm_service._calculate_content_chars(norm)
        self.assertLessEqual(compacted_chars, llm_service.MAX_INPUT_CHARS_PER_CALL)



