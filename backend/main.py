from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from routes.api import router
from routes.auth import router as auth_router
from services.auth_store import initialize
from contextlib import asynccontextmanager
import config
import logging
import uvicorn

@asynccontextmanager
async def lifespan(app):
    initialize()
    yield


app = FastAPI(title="GiTMCP", description="AI-powered GitHub Repository Intelligence", version="1.1.0", lifespan=lifespan)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_origin_regex=r".*",
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(router)
app.include_router(auth_router)


@app.exception_handler(Exception)
async def global_exception_handler(request, exc):
    logging.getLogger("uvicorn.error").error(f"Unhandled server exception on {request.url.path}: {exc}", exc_info=True)
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=500,
        content={"detail": "An internal server error occurred. Please try again."},
    )


from starlette.datastructures import MutableHeaders


class PrivateHeadersMiddleware:
    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def send_with_headers(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers["Cache-Control"] = "no-store"
                headers["Referrer-Policy"] = "no-referrer"
                headers["X-Content-Type-Options"] = "nosniff"
            await send(message)

        await self.app(scope, receive, send_with_headers)


app.add_middleware(PrivateHeadersMiddleware)


class OAuthAccessLogFilter(logging.Filter):
    def filter(self, record):
        if isinstance(record.args, tuple) and len(record.args) == 5:
            args = list(record.args)
            if str(args[2]).startswith("/auth/"):
                args[2] = str(args[2]).split("?", 1)[0]
                record.args = tuple(args)
        return True


logging.getLogger("uvicorn.access").addFilter(OAuthAccessLogFilter())
# SDK wire logs may contain private tool output; do not enable them in production.
for name in ("mcp", "httpx", "httpcore"):
    logging.getLogger(name).setLevel(logging.CRITICAL)


@app.get("/")
def root():
    return {"message": "GiTMCP API is running", "docs": "/docs"}


if __name__ == "__main__":
    uvicorn.run("main:app", host="localhost", port=8000, reload=True)
