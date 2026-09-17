"""Exercise authenticated MCP routes and main middleware."""
import importlib
import logging
import sys
import unittest
from unittest.mock import Mock, patch

from fastapi import FastAPI
from fastapi.testclient import TestClient
import test_auth
from routes import auth
import config


class RouteTests(unittest.TestCase):
    def setUp(self):
        test_auth.AuthTests.setUp(self)
        sys.modules.pop("routes.api", None)
        self.routes = importlib.import_module("routes.api")
        app = FastAPI()
        app.include_router(auth.router)
        app.include_router(self.routes.router)
        self.a.close(); self.b.close()
        self.a, self.b = TestClient(app), TestClient(app)

    def tearDown(self):
        sys.modules.pop("routes.api", None)
        test_auth.AuthTests.tearDown(self)

    start = test_auth.AuthTests.start
    finish = test_auth.AuthTests.finish
    login = test_auth.AuthTests.login

    def test_mcp_query_routes_authentication(self):
        a = self.login(self.a, 1, "token-a")
        a_headers = {"Origin": config.FRONTEND_URL, "X-CSRF-Token": a["csrf_token"]}

        with patch("services.github_api.GitHubAPI.repository", return_value={"full_name": "owner/repo"}), \
             patch.object(self.routes, "answer_question_with_mcp", return_value={"answer": "MCP Response", "steps": []}) as mock_mcp:
            # Authenticated query
            res = self.a.post("/query/mcp", json={"github_repo": "owner/repo", "question": "What is this?"}, headers=a_headers)
            self.assertEqual(res.status_code, 200, res.text)
            self.assertEqual(res.json()["answer"], "MCP Response")
            self.assertEqual(mock_mcp.call_count, 1)

            # /chat alias
            res_chat = self.a.post("/chat", json={"github_repo": "owner/repo", "question": "What is this?"}, headers=a_headers)
            self.assertEqual(res_chat.status_code, 200, res_chat.text)
            self.assertEqual(res_chat.json()["answer"], "MCP Response")

    def test_all_data_routes_require_login_and_oauth_logs_hide_queries(self):
        for path, body in [("/query/mcp", {"github_repo": "a/b", "question": "q"}),
                           ("/chat", {"github_repo": "a/b", "question": "q"})]:
            self.assertEqual(self.a.post(path, json=body).status_code, 401)

        sys.modules.pop("main", None)
        main = importlib.import_module("main")
        record = logging.LogRecord("uvicorn.access", logging.INFO, "", 0, '%s %s %s %s %s',
            ("client", "GET", "/auth/github/callback?code=secret-code&state=secret-state", "1.1", 303), None)
        main.OAuthAccessLogFilter().filter(record)
        self.assertNotIn("secret", record.getMessage())
        self.assertEqual(main.app.title, "GiTMCP")
        sys.modules.pop("main", None)

