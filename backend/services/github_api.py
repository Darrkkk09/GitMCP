"""GitHub REST for OAuth/account administration and authorized source downloads.

Agent tools live in github_mcp.py and use the actual MCP protocol.
"""
import re
from urllib.parse import urlparse
import requests
from fastapi import HTTPException
from services import auth_store

API = "https://api.github.com"


def parse_repo_identifier(value):
    value = value.strip().rstrip("/")
    if "://" in value:
        parsed = urlparse(value)
        if parsed.scheme != "https" or parsed.netloc != "github.com" or parsed.query or parsed.fragment:
            raise ValueError("Use an https://github.com/owner/repo URL")
        value = parsed.path.lstrip("/")
    value = value.removesuffix(".git")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9-]*/[A-Za-z0-9_.-]+", value):
        raise ValueError("Use owner/repo or https://github.com/owner/repo")
    owner, repo = value.split("/")
    if repo in {".", ".."}:
        raise ValueError("Invalid repository")
    return owner, repo


class GitHubAPI:
    def __init__(self, user_id):
        self.user_id = user_id

    def token(self):
        try:
            return auth_store.access_token(self.user_id)
        except ValueError as exc:
            raise HTTPException(401, str(exc)) from None

    def request(self, path, **kwargs):
        try:
            response = requests.get(API + path, headers={
                "Authorization": f"Bearer {self.token()}",
                "Accept": "application/vnd.github+json",
                "X-GitHub-Api-Version": "2022-11-28",
            }, timeout=30, allow_redirects=False, **kwargs)
        except requests.RequestException:
            raise HTTPException(502, "GitHub is unavailable. Please try again.") from None
        if response.status_code == 401:
            auth_store.disconnect(self.user_id)
            raise HTTPException(401, "GitHub authorization was revoked. Reconnect GitHub.")
        if response.status_code == 403:
            raise HTTPException(403, "GitHub denied access or rate limited this request. Check scopes and organization approval.")
        if response.status_code == 404:
            raise HTTPException(404, "Repository is unavailable to your GitHub account.")
        if response.status_code not in (200, 302):
            raise HTTPException(502, "GitHub request failed.")
        return response

    def repository(self, name):
        owner, repo = parse_repo_identifier(name)
        response = self.request(f"/repos/{owner}/{repo}")
        if response.status_code != 200:
            raise HTTPException(409, "Repository moved. Select its current name.")
        return response.json()

    def repositories(self, page):
        response = self.request("/user/repos", params={
            "per_page": 50, "page": page, "sort": "updated",
            "affiliation": "owner,collaborator,organization_member"})
        items = response.json()
        return {"repositories": [{k: item.get(k) for k in
                ("id", "full_name", "private", "html_url", "default_branch", "description")}
                for item in items], "next_page": page + 1 if 'rel="next"' in response.headers.get("Link", "") else None}
