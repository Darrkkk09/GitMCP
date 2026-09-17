"""Persistent identities, opaque sessions and encrypted GitHub authorizations."""
from contextlib import contextmanager
import hashlib
import secrets
import sqlite3
import time
from uuid import uuid4

from cryptography.fernet import Fernet
import config


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def cipher():
    if not config.TOKEN_ENCRYPTION_KEY:
        raise RuntimeError("TOKEN_ENCRYPTION_KEY must be configured")
    return Fernet(config.TOKEN_ENCRYPTION_KEY.encode())


@contextmanager
def database():
    config.AUTH_DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    db = sqlite3.connect(config.AUTH_DB_PATH, timeout=15)
    db.row_factory = sqlite3.Row
    db.execute("PRAGMA foreign_keys=ON")
    try:
        with db:
            yield db
    finally:
        db.close()


def initialize():
    cipher()  # Fail closed; never generate an ephemeral encryption key on startup.
    with database() as db:
        db.executescript("""
        CREATE TABLE IF NOT EXISTS users (
            id TEXT PRIMARY KEY, created_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS github_connections (
            user_id TEXT PRIMARY KEY REFERENCES users(id),
            github_user_id TEXT UNIQUE NOT NULL, github_login TEXT NOT NULL,
            encrypted_access_token TEXT, scopes TEXT NOT NULL DEFAULT '',
            expires_at INTEGER, created_at INTEGER NOT NULL, updated_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS sessions (
            id_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
            csrf TEXT NOT NULL, expires_at INTEGER NOT NULL
        );
        CREATE TABLE IF NOT EXISTS oauth_states (
            state_hash TEXT PRIMARY KEY, browser_hash TEXT NOT NULL,
            verifier TEXT NOT NULL, user_id TEXT, session_hash TEXT,
            expires_at INTEGER NOT NULL
        );
        """)


def get_session(raw):
    if not raw:
        return None
    with database() as db:
        row = db.execute("SELECT * FROM sessions WHERE id_hash=? AND expires_at>?",
                         (digest(raw), int(time.time()))).fetchone()
        return dict(row) if row else None


def create_session(user_id, old_session=None):
    raw, csrf = secrets.token_urlsafe(32), secrets.token_urlsafe(32)
    with database() as db:
        db.execute("DELETE FROM sessions WHERE expires_at<=?", (int(time.time()),))
        if old_session:
            db.execute("DELETE FROM sessions WHERE id_hash=?", (digest(old_session),))
        db.execute("INSERT INTO sessions VALUES (?, ?, ?, ?)",
                   (digest(raw), user_id, csrf, int(time.time()) + config.SESSION_TTL_SECONDS))
    return raw


def delete_session(raw):
    with database() as db:
        db.execute("DELETE FROM sessions WHERE id_hash=?", (digest(raw or ""),))


def begin_oauth(session=None):
    state, browser, verifier = (secrets.token_urlsafe(32) for _ in range(3))
    with database() as db:
        db.execute("DELETE FROM oauth_states WHERE expires_at<=?", (int(time.time()),))
        db.execute("INSERT INTO oauth_states VALUES (?, ?, ?, ?, ?, ?)", (
            digest(state), digest(browser), verifier,
            session["user_id"] if session else None,
            session["id_hash"] if session else None, int(time.time()) + 600))
    return state, browser, verifier


def consume_oauth(state, browser, session):
    if not state or not browser:
        raise ValueError("Invalid OAuth state")
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        row = db.execute("SELECT * FROM oauth_states WHERE state_hash=?", (digest(state),)).fetchone()
        if not row or row["expires_at"] <= time.time():
            raise ValueError("Invalid OAuth state")
        if not secrets.compare_digest(row["browser_hash"], digest(browser)):
            raise ValueError("Invalid OAuth state")
        if row["session_hash"] != (session["id_hash"] if session else None):
            raise ValueError("Login session changed")
        db.execute("DELETE FROM oauth_states WHERE state_hash=?", (digest(state),))
        return dict(row)


def connect_github(profile, token_data, current_user_id=None):
    github_id, login = str(profile["id"]), profile["login"]
    encrypted = cipher().encrypt(token_data["access_token"].encode()).decode()
    now = int(time.time())
    expires = now + int(token_data["expires_in"]) if token_data.get("expires_in") else None
    with database() as db:
        db.execute("BEGIN IMMEDIATE")
        existing = db.execute("SELECT * FROM github_connections WHERE github_user_id=?", (github_id,)).fetchone()
        if current_user_id:
            own = db.execute("SELECT github_user_id FROM github_connections WHERE user_id=?", (current_user_id,)).fetchone()
            if (own and own[0] != github_id) or (existing and existing["user_id"] != current_user_id):
                raise ValueError("Sign out before connecting a different GitHub account")
        user_id = current_user_id or (existing["user_id"] if existing else uuid4().hex)
        db.execute("INSERT OR IGNORE INTO users VALUES (?, ?)", (user_id, now))
        db.execute("""INSERT INTO github_connections VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_id) DO UPDATE SET github_login=excluded.github_login,
            encrypted_access_token=excluded.encrypted_access_token, scopes=excluded.scopes,
            expires_at=excluded.expires_at, updated_at=excluded.updated_at""",
            (user_id, github_id, login, encrypted, token_data.get("scope", ""), expires, now, now))
    return user_id


def connection(user_id):
    with database() as db:
        row = db.execute("SELECT * FROM github_connections WHERE user_id=?", (user_id,)).fetchone()
        return dict(row) if row else None


def access_token(user_id):
    row = connection(user_id)
    if not row or not row["encrypted_access_token"]:
        raise ValueError("Connect GitHub to continue")
    if row["expires_at"] and row["expires_at"] <= time.time():
        raise ValueError("GitHub authorization expired. Reconnect GitHub")
    return cipher().decrypt(row["encrypted_access_token"].encode()).decode()


def disconnect(user_id):
    with database() as db:
        db.execute("UPDATE github_connections SET encrypted_access_token=NULL, updated_at=? WHERE user_id=?",
                   (int(time.time()), user_id))
        db.execute("DELETE FROM oauth_states WHERE user_id=?", (user_id,))
