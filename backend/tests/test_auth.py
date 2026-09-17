"""OAuth provider calls are mocked; storage, cookies, state and authorization are real."""
from pathlib import Path
import sys
import time
from urllib.parse import urlparse, parse_qs
import unittest
from unittest.mock import Mock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from cryptography.fernet import Fernet
from fastapi import FastAPI
from fastapi.testclient import TestClient
import tempfile
import config
from routes import auth
from services import auth_store
from services.github_api import GitHubAPI, parse_repo_identifier


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.root = Path(self.directory.__enter__())
        self.settings = patch.multiple(config, AUTH_DB_PATH=self.root / "auth.sqlite3",
            TOKEN_ENCRYPTION_KEY=Fernet.generate_key().decode(), GITHUB_CLIENT_ID="test-client",
            GITHUB_CLIENT_SECRET="test-secret", COOKIE_SECURE=False,
            FRONTEND_URL="http://localhost:5173", GITHUB_PRIVATE_REPOS=False)
        self.settings.start()
        auth_store.initialize()
        app = FastAPI()
        app.include_router(auth.router)
        self.a, self.b = TestClient(app), TestClient(app)

    def tearDown(self):
        self.a.close(); self.b.close()
        self.settings.stop()
        self.directory.__exit__(None, None, None)

    def start(self, browser):
        response = browser.get("/auth/github", follow_redirects=False)
        self.assertEqual(response.status_code, 302)
        return parse_qs(urlparse(response.headers["location"]).query)

    def finish(self, browser, state, github_id, token):
        with patch.object(auth.requests, "post", return_value=Mock(status_code=200,
            json=lambda: {"access_token": token, "token_type": "bearer", "scope": "repo"})) as exchange, \
            patch.object(auth.requests, "get", return_value=Mock(status_code=200,
            json=lambda: {"id": github_id, "login": f"user-{github_id}"})):
            response = browser.get("/auth/github/callback", params={"state": state, "code": "test-code"}, follow_redirects=False)
        return response, exchange

    def login(self, browser, github_id, token):
        start = self.start(browser)
        response, _ = self.finish(browser, start["state"][0], github_id, token)
        self.assertIn("auth=connected", response.headers["location"])
        return browser.get("/auth/me").json()

    def test_two_users_encrypted_persistence_and_repository_credentials(self):
        a = self.login(self.a, 1, "token-user-a")
        b = self.login(self.b, 2, "token-user-b")
        self.assertNotEqual(a["user"]["id"], b["user"]["id"])
        self.assertNotIn("token-user", str(a) + str(b))
        self.assertNotIn(b"token-user", config.AUTH_DB_PATH.read_bytes())
        auth_store.initialize()  # Simulate restart against the same durable database/key.
        self.assertEqual(self.a.get("/auth/me").status_code, 200)
        def github(url, headers, **kwargs):
            who = "a" if headers["Authorization"] == "Bearer token-user-a" else "b"
            return Mock(status_code=200, headers={}, json=lambda: [{
                "id": who, "full_name": f"{who}/private", "private": True}])
        with patch("services.github_api.requests.get", side_effect=github):
            self.assertEqual(self.a.get("/github/repos").json()["repositories"][0]["full_name"], "a/private")
            self.assertEqual(self.b.get("/github/repos?user_id=" + a["user"]["id"]).json()["repositories"][0]["full_name"], "b/private")

    def test_state_is_single_use_and_pkce(self):
        start = self.start(self.a)
        self.assertEqual(start["code_challenge_method"], ["S256"])
        # State is single-use: first use succeeds
        response, exchange = self.finish(self.a, start["state"][0], 1, "secret-a")
        self.assertIn("auth=connected", response.headers["location"])
        self.assertIn("code_verifier", exchange.call_args.kwargs["data"])
        # Second use of same state fails (single-use)
        response, exchange = self.finish(self.a, start["state"][0], 1, "secret-a")
        self.assertIn("auth=error", response.headers["location"])
        exchange.assert_not_called()

    def test_invalid_expired_denied_callback(self):
        response, exchange = self.finish(self.a, "invalid", 1, "a")
        exchange.assert_not_called()
        self.assertIn("auth=error", response.headers["location"])
        start = self.start(self.a)
        with auth_store.database() as db:
            db.execute("UPDATE oauth_states SET expires_at=0")
        _, exchange = self.finish(self.a, start["state"][0], 1, "a")
        exchange.assert_not_called()
        start = self.start(self.a)
        response = self.a.get("/auth/github/callback", params={"state": start["state"][0], "error": "access_denied"}, follow_redirects=False)
        self.assertIn("auth=error", response.headers["location"])
        self.assertEqual(self.a.get("/auth/me").status_code, 401)

    def test_account_switch_cannot_rebind_existing_user(self):
        a = self.login(self.a, 1, "token-a")
        start = self.start(self.a)
        response, _ = self.finish(self.a, start["state"][0], 2, "token-b")
        self.assertIn("auth=error", response.headers["location"])
        self.assertEqual(auth_store.access_token(a["user"]["id"]), "token-a")

    def test_csrf_disconnect_and_logout(self):
        a = self.login(self.a, 1, "token-a")
        b = self.login(self.b, 2, "token-b")
        self.assertEqual(self.a.delete("/auth/github").status_code, 403)
        headers = {"Origin": config.FRONTEND_URL, "X-CSRF-Token": a["csrf_token"]}
        with patch.object(auth.requests, "delete", return_value=Mock(status_code=204)) as revoke:
            response = self.a.delete("/auth/github", headers=headers)
        self.assertEqual(response.status_code, 200)
        self.assertNotIn("token-a", response.text)
        self.assertEqual(revoke.call_args.kwargs["json"], {"access_token": "token-a"})
        with self.assertRaises(ValueError):
            auth_store.access_token(a["user"]["id"])
        self.assertEqual(auth_store.access_token(b["user"]["id"]), "token-b")
        self.assertEqual(self.a.post("/auth/logout", headers=headers).status_code, 200)
        self.assertEqual(self.a.get("/auth/me").status_code, 401)

    def test_expiry_revocation_and_missing_repo_fail_closed(self):
        a = self.login(self.a, 1, "token-a")
        api = GitHubAPI(a["user"]["id"])
        with patch("services.github_api.requests.get", return_value=Mock(status_code=404)):
            with self.assertRaises(Exception) as caught:
                api.repository("other/private")
            self.assertEqual(caught.exception.status_code, 404)
        with patch("services.github_api.requests.get", return_value=Mock(status_code=401)):
            with self.assertRaises(Exception) as caught:
                api.repository("user-1/private")
            self.assertEqual(caught.exception.status_code, 401)
        with self.assertRaises(ValueError):
            auth_store.access_token(a["user"]["id"])

    def test_expired_token_requires_reconnection(self):
        a = self.login(self.a, 1, "token-a")
        with auth_store.database() as db:
            db.execute("UPDATE github_connections SET expires_at=1 WHERE user_id=?", (a["user"]["id"],))
        self.assertFalse(self.a.get("/auth/me").json()["github_connected"])
        with self.assertRaises(ValueError):
            auth_store.access_token(a["user"]["id"])

    def test_repository_url_validation(self):
        for value in ["https://evil.test/a/b", "https://github.com@evil.test/a/b", "owner/..", "file:///etc/passwd", "https://github.com/a/b?token=x"]:
            with self.assertRaises(ValueError):
                parse_repo_identifier(value)
        self.assertEqual(parse_repo_identifier("https://github.com/owner/repo.git"), ("owner", "repo"))


if __name__ == "__main__":
    unittest.main()
