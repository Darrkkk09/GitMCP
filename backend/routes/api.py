from fastapi import APIRouter, HTTPException, Depends
from starlette.concurrency import run_in_threadpool
from routes.auth import current_user
from services.github_api import GitHubAPI
from models.schemas import MCPQueryRequest
from services.github_mcp import parse_repo_identifier
from services.llm_service import answer_question_with_mcp
import logging

logger = logging.getLogger(__name__)

router = APIRouter()


def _unwrap_exception(exc: BaseException) -> BaseException:
    while hasattr(exc, "exceptions") and getattr(exc, "exceptions"):
        exc = exc.exceptions[0]
    return exc


@router.post("/query/mcp")
@router.post("/chat")
async def query_repository_mcp(request: MCPQueryRequest, user_id=Depends(current_user)):
    try:
        full_name = await run_in_threadpool(authorized_repo, request.github_repo, user_id)
        owner, repo = parse_repo_identifier(full_name)
    except HTTPException:
        raise
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    except Exception as exc:
        logger.error(f"[Agent] Repository authorization failed: {exc}", exc_info=True)
        raise HTTPException(status_code=400, detail="Invalid repository identifier or unauthorized access.") from None

    try:
        result = await answer_question_with_mcp(
            user_id=user_id,
            owner=owner,
            repo=repo,
            question=request.question,
            history=request.history,
        )
        return {
            "mode": "github_mcp",
            "repo_name": f"{owner}/{repo}",
            "question": request.question,
            "answer": result.get("answer"),
            "steps": result.get("steps", [])
        }
    except BaseException as exc:
        real_exc = _unwrap_exception(exc)
        if isinstance(real_exc, HTTPException):
            logger.error(f"[Agent] HTTP error during /query/mcp: {real_exc.status_code} - {real_exc.detail}")
            raise real_exc
        logger.error(f"[Agent] Unexpected exception during /query/mcp: {real_exc}", exc_info=True)
        raise HTTPException(502, f"GitHub MCP query failed: {str(real_exc)}") from None


def authorized_repo(value, user_id):
    try:
        info = GitHubAPI(user_id).repository(value)
        owner, repo = parse_repo_identifier(info["full_name"])
        return f"{owner}/{repo}".lower()
    except ValueError:
        raise HTTPException(400, "Use owner/repo or https://github.com/owner/repo") from None
