import base64
import hashlib
import secrets
import time
from urllib.parse import urlencode
import requests
from fastapi import APIRouter, Depends, HTTPException, Request, Query
from fastapi.responses import RedirectResponse
import config
from services import auth_store
from services.github_api import GitHubAPI

router = APIRouter()
SESSION_COOKIE = "gitmcp_session"
OAUTH_COOKIE = "gitmcp_oauth"


def current_user(request: Request):
    session = auth_store.get_session(request.cookies.get(SESSION_COOKIE))
    if not session:
        exc = HTTPException(401, "Connect GitHub to sign in")
        exc.headers = {"Set-Cookie": f"{SESSION_COOKIE}=; Path=/; Max-Age=0; HttpOnly; SameSite=lax"}
        raise exc
    if request.method not in {"GET", "HEAD", "OPTIONS"}:
        if not secrets.compare_digest(request.headers.get("x-csrf-token", ""), session["csrf"]):
            raise HTTPException(403, "Invalid CSRF token")
    return session["user_id"]


def cookie(response, name, value, max_age):
    response.set_cookie(name, value, max_age=max_age, httponly=True,
                        secure=config.COOKIE_SECURE, samesite="lax", path="/")


@router.get("/auth/github")
def github_login(request: Request):
    if not config.GITHUB_CLIENT_ID or not config.GITHUB_CLIENT_SECRET:
        raise HTTPException(503, "GitHub OAuth is not configured")
    session = auth_store.get_session(request.cookies.get(SESSION_COOKIE))
    state, browser, verifier = auth_store.begin_oauth(session)
    challenge = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    # Encode browser token into state so it survives the cross-site redirect from GitHub
    # (Chrome blocks SameSite=Lax cookies on cross-site redirects)
    combined_state = f"{state}:{browser}"
    return RedirectResponse("https://github.com/login/oauth/authorize?" + urlencode({
        "client_id": config.GITHUB_CLIENT_ID, "redirect_uri": config.GITHUB_REDIRECT_URI,
        "scope": "repo" if config.GITHUB_PRIVATE_REPOS else "",
        "state": combined_state, "code_challenge": challenge, "code_challenge_method": "S256",
    }), status_code=302)


@router.get("/auth/github/callback")
def github_callback(request: Request, state: str = "", code: str = "", error: str = ""):
    import logging
    log = logging.getLogger("auth.callback")
    actual_state, _, browser = state.partition(":")

    def error_redirect(reason=""):
        log.error(f"OAuth failed: {reason}")
        return RedirectResponse(config.FRONTEND_URL + "/?auth=error", status_code=303)

    try:
        session = auth_store.get_session(request.cookies.get(SESSION_COOKIE))
        pending = auth_store.consume_oauth(actual_state, browser, session)
        if error or not code:
            return error_redirect(f"denied:{error}")
        exchange = requests.post("https://github.com/login/oauth/access_token", headers={"Accept": "application/json"},
            data={"client_id": config.GITHUB_CLIENT_ID, "client_secret": config.GITHUB_CLIENT_SECRET,
                  "redirect_uri": config.GITHUB_REDIRECT_URI, "code": code,
                  "code_verifier": pending["verifier"]}, timeout=20)
        if exchange.status_code != 200:
            return error_redirect(f"token_exchange:{exchange.status_code}")
        token_data = exchange.json()
        if not token_data.get("access_token") or token_data.get("token_type", "").lower() != "bearer":
            return error_redirect(f"no_token:{token_data.get('error', 'unknown')}")
        profile = requests.get("https://api.github.com/user", headers={
            "Authorization": "Bearer " + token_data["access_token"], "Accept": "application/vnd.github+json"}, timeout=20)
        if profile.status_code != 200:
            return error_redirect(f"profile:{profile.status_code}")
        user_id = auth_store.connect_github(profile.json(), token_data, pending["user_id"])
        raw = auth_store.create_session(user_id, request.cookies.get(SESSION_COOKIE))
        response = RedirectResponse(config.FRONTEND_URL + "/?auth=connected", status_code=303)
        cookie(response, SESSION_COOKIE, raw, config.SESSION_TTL_SECONDS)
        log.info(f"OAuth SUCCESS for {profile.json().get('login')}")
        return response
    except (ValueError, KeyError, requests.RequestException) as exc:
        return error_redirect(f"{type(exc).__name__}:{exc}")


@router.get("/auth/me")
def me(request: Request, user_id=Depends(current_user)):
    session = auth_store.get_session(request.cookies.get(SESSION_COOKIE))
    row = auth_store.connection(user_id)
    return {"user": {"id": user_id, "github_login": row["github_login"]},
            "github_connected": bool(row["encrypted_access_token"]) and
                (not row["expires_at"] or row["expires_at"] > time.time()), "csrf_token": session["csrf"]}


@router.get("/github/repos")
def github_repos(page: int = Query(1, ge=1, le=1000), user_id=Depends(current_user)):
    return GitHubAPI(user_id).repositories(page)


@router.post("/auth/logout")
def logout(request: Request, user_id=Depends(current_user)):
    from fastapi.responses import JSONResponse
    auth_store.delete_session(request.cookies.get(SESSION_COOKIE))
    response = JSONResponse({"message": "Signed out"})
    response.delete_cookie(SESSION_COOKIE, path="/")
    return response


@router.delete("/auth/github")
def disconnect(user_id=Depends(current_user)):
    revoked = False
    try:
        token = auth_store.access_token(user_id)
        response = requests.delete(f"https://api.github.com/applications/{config.GITHUB_CLIENT_ID}/token",
            auth=(config.GITHUB_CLIENT_ID, config.GITHUB_CLIENT_SECRET),
            json={"access_token": token}, timeout=20)
        revoked = response.status_code in (204, 404)
    except (ValueError, requests.RequestException):
        pass
    finally:
        auth_store.disconnect(user_id)
    return {"message": "GitHub disconnected", "revoked_on_github": revoked}
