import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")

def get_gemini_api_keys():
    keys = []
    for k in ["GEMINI_API_KEY", "GEMINI_API_KEY1", "GEMINI_API_KEY2", "GEMINI_API_KEY3", "GEMINI_API_KEY4"]:
        val = os.getenv(k)
        if val and val.strip() and val.strip() not in keys:
            keys.append(val.strip())
    extra = os.getenv("GEMINI_API_KEYS")
    if extra:
        for key in extra.split(","):
            key = key.strip()
            if key and key not in keys:
                keys.append(key)
    return keys


def get_groq_api_keys():
    keys = []
    for k in ["GROQ_API_KEY", "GROQ_API_KEY1", "GROQ_API_KEY2", "GROQ_API_KEY3"]:
        val = os.getenv(k)
        if val and val.strip() and val.strip() not in keys:
            keys.append(val.strip())
    extra = os.getenv("GROQ_API_KEYS")
    if extra:
        for key in extra.split(","):
            key = key.strip()
            if key and key not in keys:
                keys.append(key)
    return keys


GEMINI_API_KEYS = get_gemini_api_keys()
GEMINI_API_KEY = GEMINI_API_KEYS[0] if GEMINI_API_KEYS else None
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")

GROQ_API_KEYS = get_groq_api_keys()
GROQ_MODEL = os.getenv("GROQ_MODEL", "groq/compound")
GITHUB_CLIENT_ID = os.getenv("GITHUB_CLIENT_ID", "")
GITHUB_CLIENT_SECRET = os.getenv("GITHUB_CLIENT_SECRET", "")
GITHUB_REDIRECT_URI = os.getenv("GITHUB_REDIRECT_URI", "http://localhost:8000/auth/github/callback")
GITHUB_PRIVATE_REPOS = os.getenv("GITHUB_PRIVATE_REPOS", "false").lower() == "true"
FRONTEND_URL = os.getenv("FRONTEND_URL", "http://localhost:5173").rstrip("/")
COOKIE_SECURE = os.getenv("COOKIE_SECURE", "false").lower() == "true"
TOKEN_ENCRYPTION_KEY = os.getenv("TOKEN_ENCRYPTION_KEY", "")
AUTH_DB_PATH = Path(os.getenv("AUTH_DB_PATH", str(BASE_DIR / "data" / "auth.sqlite3")))
SESSION_TTL_SECONDS = 60 * 60 * 24 * 7
