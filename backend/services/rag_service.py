import os
import sys
import shutil
import logging
import subprocess
import re
from pathlib import Path
from typing import List, Dict, Any, Optional, Union

# Python 3.14 C-extension protobuf workaround for ChromaDB compatibility
sys.modules['google._upb._message'] = None
import chromadb
from config import BASE_DIR
from services.llm_service import _generate_content_with_fallback
from google.genai import types

logger = logging.getLogger(__name__)

DATA_DIR = BASE_DIR / "data"
CLONE_DIR = DATA_DIR / "cloned_repos"
CHROMA_DIR = DATA_DIR / "chroma_db"

CLONE_DIR.mkdir(parents=True, exist_ok=True)
CHROMA_DIR.mkdir(parents=True, exist_ok=True)

# Singleton ChromaDB client
_chroma_client = None

def get_chroma_client():
    global _chroma_client
    if _chroma_client is None:
        _chroma_client = chromadb.PersistentClient(path=str(CHROMA_DIR))
    return _chroma_client

def _sanitize_collection_name(user_id: str, owner: str, repo: str) -> str:
    raw = f"rag_{user_id}_{owner}_{repo}".lower()
    cleaned = re.sub(r'[^a-z0-9_\-]', '_', raw)
    cleaned = re.sub(r'_+', '_', cleaned).strip('_')
    if len(cleaned) < 3:
        cleaned = f"col_{cleaned}"
    return cleaned[:63]

def get_user_collections(user_id: str) -> List[Dict[str, str]]:
    client = get_chroma_client()
    prefix = f"rag_{user_id}_".lower()
    results = []
    for col in client.list_collections():
        cname = col.name.lower()
        if cname.startswith(prefix):
            parts = cname[len(prefix):].split("_", 1)
            if len(parts) == 2:
                owner, repo = parts[0], parts[1]
            else:
                owner, repo = "user", cname[len(prefix):]
            results.append({"name": col.name, "owner": owner, "repo": repo})
    return results

def get_repo_clone_path(user_id: str, owner: str, repo: str) -> Path:
    return CLONE_DIR / str(user_id) / f"{owner}_{repo}"

def clone_or_update_repo(user_id: str, owner: str, repo: str, token: Optional[str] = None) -> Path:
    target_path = get_repo_clone_path(user_id, owner, repo)
    
    if token:
        clone_url = f"https://x-access-token:{token}@github.com/{owner}/{repo}.git"
    else:
        clone_url = f"https://github.com/{owner}/{repo}.git"
        
    if target_path.exists() and (target_path / ".git").exists():
        logger.info(f"[RAG] Repo already exists at {target_path}, running git pull...")
        try:
            subprocess.run(
                ["git", "pull", "--ff-only"],
                cwd=str(target_path),
                capture_output=True,
                text=True,
                timeout=60,
                check=False
            )
            return target_path
        except Exception as exc:
            logger.warning(f"[RAG] Git pull failed: {exc}, re-cloning repo...")
            shutil.rmtree(target_path, ignore_errors=True)
            
    logger.info(f"[RAG] Cloning repo {owner}/{repo} into {target_path}...")
    target_path.parent.mkdir(parents=True, exist_ok=True)
    
    try:
        res = subprocess.run(
            ["git", "clone", "--depth", "1", clone_url, str(target_path)],
            capture_output=True,
            text=True,
            timeout=120,
            check=True
        )
        logger.info(f"[RAG] Successfully cloned {owner}/{repo}")
        return target_path
    except subprocess.CalledProcessError as exc:
        err_msg = exc.stderr or exc.stdout or str(exc)
        logger.error(f"[RAG] Git clone failed: {err_msg}")
        raise ValueError(f"Failed to clone repository '{owner}/{repo}'. Check repository name and access permissions.") from None
    except Exception as exc:
        logger.error(f"[RAG] Unexpected error during clone: {exc}")
        raise ValueError(f"Repository cloning error: {str(exc)}") from None

# Ignored patterns and file extensions
IGNORED_DIRS = {
    ".git", "node_modules", "venv", ".venv", "__pycache__", ".pytest_cache",
    "dist", "build", ".next", ".nuxt", "data", "vendor", "target", "out",
    ".idea", ".vscode", "coverage"
}

IGNORED_EXTENSIONS = {
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".pdf", ".zip", ".tar",
    ".gz", ".rar", ".7z", ".exe", ".dll", ".so", ".dylib", ".pyc", ".pyo",
    ".ico", ".woff", ".woff2", ".ttf", ".eot", ".mp4", ".mp3", ".wav", ".avi",
    ".mov", ".db", ".sqlite", ".sqlite3", ".bin", ".dat", ".lock", ".svg"
}

def chunk_repository(repo_dir: Path, chunk_size: int = 1200, overlap: int = 200) -> tuple[List[Dict[str, Any]], int]:
    chunks = []
    file_count = 0
    
    for root, dirs, files in os.walk(repo_dir):
        dirs[:] = [d for d in dirs if d not in IGNORED_DIRS and not d.startswith(".")]
        
        for file_name in files:
            ext = Path(file_name).suffix.lower()
            if ext in IGNORED_EXTENSIONS or file_name.startswith("."):
                continue
                
            full_path = Path(root) / file_name
            try:
                rel_path = full_path.relative_to(repo_dir).as_posix()
            except Exception:
                rel_path = file_name
                
            try:
                with open(full_path, "r", encoding="utf-8", errors="ignore") as f:
                    lines = f.readlines()
            except Exception as e:
                logger.warning(f"[RAG] Skipping unreadable file {rel_path}: {e}")
                continue
                
            if not lines:
                continue
                
            file_count += 1
            full_text = "".join(lines)
            if not full_text.strip():
                continue
                
            total_chars = len(full_text)
            start_char = 0
            chunk_idx = 0
            
            while start_char < total_chars:
                end_char = min(start_char + chunk_size, total_chars)
                chunk_text = full_text[start_char:end_char]
                
                # Approximate line numbers
                start_line = full_text[:start_char].count('\n') + 1
                end_line = full_text[:end_char].count('\n') + 1
                
                chunk_id = f"{rel_path}:{chunk_idx}"
                chunks.append({
                    "id": chunk_id,
                    "text": f"File: {rel_path} (Lines {start_line}-{end_line})\n```\n{chunk_text}\n```",
                    "raw_text": chunk_text,
                    "metadata": {
                        "file_path": rel_path,
                        "start_line": start_line,
                        "end_line": end_line,
                        "chunk_idx": chunk_idx,
                    }
                })
                
                chunk_idx += 1
                start_char += (chunk_size - overlap)
                if start_char >= total_chars:
                    break
                    
    return chunks, file_count

def ingest_repository_rag(user_id: str, owner: str, repo: str, token: Optional[str] = None) -> Dict[str, Any]:
    client = get_chroma_client()
    col_name = _sanitize_collection_name(user_id, owner, repo)
    
    logger.info(f"[RAG] Starting RAG ingestion for {owner}/{repo} (Collection: {col_name})...")
    repo_path = clone_or_update_repo(user_id, owner, repo, token)
    chunks, file_count = chunk_repository(repo_path)
    
    if not chunks:
        raise ValueError(f"No indexable text files found in repository {owner}/{repo}.")
        
    try:
        client.delete_collection(col_name)
    except Exception:
        pass
        
    collection = client.create_collection(name=col_name)
    
    # Add repo metadata to each chunk
    for c in chunks:
        c["metadata"]["repo"] = f"{owner}/{repo}"
        c["text"] = f"Repository: {owner}/{repo}\n" + c["text"]
    
    # Batch add to ChromaDB (500 items per batch)
    batch_size = 500
    for i in range(0, len(chunks), batch_size):
        batch = chunks[i:i + batch_size]
        ids = [c["id"] for c in batch]
        documents = [c["text"] for c in batch]
        metadatas = [c["metadata"] for c in batch]
        collection.add(ids=ids, documents=documents, metadatas=metadatas)
        
    logger.info(f"[RAG] Completed ingestion for {owner}/{repo}: {file_count} files, {len(chunks)} chunks.")
    return {
        "status": "success",
        "repo": f"{owner}/{repo}",
        "file_count": file_count,
        "chunk_count": len(chunks),
        "collection_name": col_name
    }

def get_rag_status(user_id: str, owner: str, repo: str) -> Dict[str, Any]:
    client = get_chroma_client()
    col_name = _sanitize_collection_name(user_id, owner, repo)
    try:
        collection = client.get_collection(col_name)
        count = collection.count()
        return {
            "indexed": count > 0,
            "chunk_count": count,
            "repo": f"{owner}/{repo}"
        }
    except Exception:
        return {
            "indexed": False,
            "chunk_count": 0,
            "repo": f"{owner}/{repo}"
        }

async def answer_question_with_rag(
    user_id: str,
    owner: str,
    repo: str,
    question: str,
    history: Optional[List[Any]] = None,
    token: Optional[str] = None
) -> Dict[str, Any]:
    steps = [f"Mode: Repository RAG (Vector Search)"]
    client = get_chroma_client()
    
    # Handle multi-repository querying if repo specifies comma-separated repos or "all"
    is_multi_repo = (repo == "all") or ("," in repo)
    target_collections = []
    
    if is_multi_repo:
        steps.append(f"Multi-repository RAG mode: searching profile repositories...")
        if repo == "all":
            cols = get_user_collections(user_id)
            target_collections = [c["name"] for c in cols]
        else:
            repos_list = [r.strip() for r in repo.split(",") if r.strip()]
            for r_item in repos_list:
                if "/" in r_item:
                    o_item, rp_item = r_item.split("/", 1)
                    target_collections.append(_sanitize_collection_name(user_id, o_item, rp_item))
    else:
        steps.append(f"Target Repository: {owner}/{repo}")
        col_name = _sanitize_collection_name(user_id, owner, repo)
        status = get_rag_status(user_id, owner, repo)
        if not status["indexed"]:
            steps.append(f"Cloning and indexing repository for vector search...")
            ingest_res = ingest_repository_rag(user_id, owner, repo, token)
            steps.append(f"Indexed {ingest_res['file_count']} files ({ingest_res['chunk_count']} chunks) in ChromaDB")
        else:
            steps.append(f"Using existing vector index ({status['chunk_count']} chunks in ChromaDB)")
        target_collections = [col_name]
        
    all_retrieved_chunks = []
    for cname in target_collections:
        try:
            col = client.get_collection(cname)
            if col.count() == 0:
                continue
            res = col.query(query_texts=[question], n_results=min(6, col.count()))
            docs = res.get("documents", [[]])[0]
            metas = res.get("metadatas", [[]])[0]
            distances = res.get("distances", [[]])[0] if res.get("distances") else [0.5] * len(docs)
            for doc, meta, dist in zip(docs, metas, distances):
                all_retrieved_chunks.append({
                    "document": doc,
                    "metadata": meta,
                    "distance": dist,
                })
        except Exception as e:
            logger.warning(f"[RAG] Collection {cname} query skipped: {e}")
            
    # Sort chunks across repositories by similarity distance
    all_retrieved_chunks.sort(key=lambda x: x.get("distance", 1.0))
    top_chunks = all_retrieved_chunks[:8]
    
    sources = []
    retrieved_context_blocks = []
    seen_files = set()
    
    for item in top_chunks:
        doc = item["document"]
        meta = item["metadata"]
        file_path = meta.get("file_path", "unknown")
        repo_name = meta.get("repo", f"{owner}/{repo}")
        start_line = meta.get("start_line", 1)
        end_line = meta.get("end_line", 1)
        
        seen_files.add(f"{repo_name}:{file_path}")
        sources.append({
            "repo": repo_name,
            "file_path": file_path,
            "start_line": start_line,
            "end_line": end_line,
            "preview": doc[:150]
        })
        retrieved_context_blocks.append(doc)
        
    for sf in sorted(seen_files):
        steps.append(f"Retrieved chunk: {sf}")
        
    steps.append(f"Synthesizing RAG answer with Gemini/Groq LLM fallback...")
    context_str = "\n\n".join(retrieved_context_blocks)
    
    # Build prompt history to support follow-up questions
    contents = []
    if history:
        for msg in history:
            m = msg if isinstance(msg, dict) else msg.model_dump()
            role = "user" if m.get("role") == "user" else "model"
            text = m.get("content", "")
            if text and text.strip():
                contents.append(types.Content(role=role, parts=[types.Part.from_text(text=text.strip())]))
                
    current_prompt = (
        f"You are GiTMCP RAG assistant analyzing code for user '{user_id}'.\n"
        f"Target Repository Context: '{owner}/{repo}'\n"
        f"Below are the most relevant code chunks retrieved from ChromaDB vector search across indexed repository files for the user's question.\n\n"
        f"--- RETRIEVED CODE CONTEXT ---\n"
        f"{context_str}\n"
        f"--- END RETRIEVED CODE CONTEXT ---\n\n"
        f"User Question: {question}\n\n"
        f"INSTRUCTIONS:\n"
        f"1. Resolve references in the conversation history (e.g., 'that function', 'this repository', 'previous route') to answer accurately.\n"
        f"2. Answer the question based on the retrieved code chunks above and conversation history.\n"
        f"3. Reference specific repository names, file paths, and line numbers when explaining code logic.\n"
        f"4. If the retrieved context is insufficient, state clearly what is missing.\n"
        f"5. Be concise, developer-focused, and accurate."
    )
    contents.append(types.Content(role="user", parts=[types.Part.from_text(text=current_prompt)]))
    
    instruction = (
        f"You are GiTMCP RAG Assistant. Provide clear, accurate developer answers using retrieved code context and conversation history."
    )
    
    config = types.GenerateContentConfig(
        system_instruction=instruction,
        temperature=0.2,
    )
    
    response = await _generate_content_with_fallback(contents, config, reason="rag_query", call_count=1)
    
    from services.llm_service import _safe_response_text
    answer_text = _safe_response_text(response) or "Failed to generate RAG response."
    
    steps.append("Final RAG analysis completed")
    
    return {
        "answer": answer_text,
        "sources": sources,
        "steps": steps
    }
