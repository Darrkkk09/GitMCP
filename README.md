# GiTMCP

**AI-powered GitHub Repository Intelligence**

GiTMCP combines the existing ChromaDB code retrieval pipeline with authenticated,
read-only access to the official GitHub MCP Server. Each user signs in with their
own GitHub account. Redis is not used.

## Architecture

- React/Vite frontend: GitHub connection, accessible repository picker, ingestion,
  multi-repository RAG, live MCP, and combined RAG + MCP questions.
- FastAPI backend: GitHub OAuth authorization-code flow with state and PKCE;
  opaque HttpOnly session cookies and CSRF-protected mutations.
- SQLite: GiTMCP users, GitHub connections with Fernet-encrypted access tokens,
  hashed session identifiers, and short-lived, single-use OAuth transactions.
- ChromaDB: persistent BAAI/bge-small-en-v1.5 embeddings, filtered by user ID and
  canonical `owner/repo`. Existing chunking and batched embedding optimizations remain.
- MCP Python SDK: initializes a fresh Streamable HTTP session with the official
  `https://api.githubcopilot.com/mcp/` server for each live query. Credentials are
  supplied only by the backend for the authenticated user. Read-only tools are
  discovered and restricted to an application allowlist and the selected repository.
- Gemini: generates answers from indexed code, live MCP tool results, or both.

GitHub REST is used explicitly for OAuth, account/repository access checks,
repository discovery, token revocation, and source archive downloads. Agent live
reads use actual MCP `initialize`, `tools/list`, and `tools/call` messages.

## Local setup

Use Python 3.11+ and a Node version supported by the installed Vite release.

1. Create a GitHub **OAuth App** in GitHub Settings > Developer settings > OAuth Apps.
   Set Homepage URL to `http://localhost:5173` and Authorization callback URL to
   `http://localhost:8000/auth/github/callback`.
2. Copy `backend/.env.example` to `backend/.env`. Set `GITHUB_CLIENT_ID`,
   `GITHUB_CLIENT_SECRET`, and `GEMINI_API_KEY`. Generate a stable encryption key:

   ```powershell
   python -c "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

   Install the backend dependencies first if cryptography is not installed.
   Put the generated value in `TOKEN_ENCRYPTION_KEY`; keep it out of Git.
3. Install and run the backend:

   ```powershell
   cd backend
   python -m venv venv
   .\venv\Scripts\Activate.ps1
   python -m pip install -r requirements.txt
   python -m uvicorn main:app --host localhost --port 8000 --reload
   ```

4. In another terminal:

   ```powershell
   cd frontend
   npm install
   npm run dev -- --host localhost
   ```

   `frontend/.env.example` documents the optional public backend URL override.
5. Open `http://localhost:5173`, choose **Connect GitHub**, authorize, and select a
   repository. Index it for RAG, use **Live GitHub MCP** for current facts, or select
   **RAG + MCP** to combine its indexed code with live tools.

Use `localhost` consistently; mixing it with `127.0.0.1` breaks cookie behavior.
Never place GitHub credentials in a `VITE_` variable.

## Private repositories and permissions

By default, no additional OAuth scope is requested. For private repositories,
set `GITHUB_PRIVATE_REPOS=true` and reconnect. This requests `repo`: GitHub OAuth
Apps do not offer a read-only scope for private source. GiTMCP still permits only
read-only MCP tools. No workflow-write, organization-admin, or email scope is requested.
Organizations may require OAuth App approval or SSO authorization.

For a deployment requiring repository-selective and genuinely read-only token
permissions, plan a GitHub App migration. That is separate from this OAuth App integration.

## Existing data

The existing `github_repositories` Chroma collection is retained. Historical
ownerless chunks are intentionally inaccessible through authenticated routes;
there is no safe automatic way to assign them to a user. Sign in and re-index.
Repository identifiers now include `owner/repo` to prevent same-name collisions.
Tokens, sessions and identities persist in `backend/data/auth.sqlite3`; keep this
file and the same encryption key across backend restarts. Chroma remains in
`backend/chroma_db` regardless of the launch directory.

## API

- `GET /auth/github`, `GET /auth/github/callback`: OAuth.
- `GET /auth/me`: safe user/connection metadata and application CSRF token.
- `POST /auth/logout`: invalidate this application session.
- `DELETE /auth/github`: revoke the token where possible and remove local authorization.
- `GET /github/repos?page=1`: paginated repositories accessible to this GitHub user.
- Existing `/ingest`, `/repos`, `/repos/{repo_name}`, `/query`, `/query/multi`,
  `/query/mcp` routes remain, now authenticated and user-scoped.
- `/query/mcp` accepts `mode: "mcp"` or `mode: "hybrid"`.

Cookie-authenticated POST/DELETE requests require the configured frontend Origin
and `X-CSRF-Token` obtained from `/auth/me`. No endpoint returns the GitHub token.

## Validation

```powershell
python -B -m unittest discover -s backend/tests -v
cd frontend
npm run build
```

Tests mock GitHub/Gemini, use isolated temporary storage, and exercise real MCP
messages against an SDK test server. They never read/write the production Chroma
collection or require personal GitHub tokens.

See [OAuth setup, two-user verification, security and deployment notes](docs/OAUTH.md)
for the live test checklist, limitations, and a file-by-file implementation map.
