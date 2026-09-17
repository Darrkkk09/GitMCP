# GiTMCP OAuth and MCP deployment guide

## Identity and authorization design

There was no existing application authentication or relational user database.
GitHub OAuth therefore establishes the GiTMCP identity. A stable GitHub numeric
user ID maps to one internal user; login names are display metadata, not identity.
`github_connections` is separate from `users` and holds one encrypted token per
user. Connecting while signed in must return the same GitHub account; sign out
to switch accounts. This prevents relinking private indexes to another identity.

OAuth transactions are browser-bound, expire after ten minutes, are consumed
once, and retain the initiating application session when connecting. PKCE uses
S256. A successful callback rotates the application session. Cookies contain
only opaque random values; their session identifiers are hashed in SQLite.
The browser receives an application CSRF token, never a GitHub token.

Every repository ingestion/query checks current access with that user's GitHub
authorization. Chroma queries and deletes always include the internal user ID.
Listing local indexes exposes only that user's metadata. Users can remove their
own indexes even if GitHub has revoked access; querying them requires current
GitHub access. Ownerless legacy chunks are not adopted automatically.

Each MCP query opens and closes its own official SDK session, with no global
GitHub token or shared authenticated HTTP client. Both the remote server's
read-only mode and a local allowlist restrict tools. Tool calls cannot change
the selected owner/repository. Tool discovery is dynamic within the allowlist;
new GitHub tools are not automatically authorized.

## Required configuration

| Variable | Meaning |
| --- | --- |
| `GITHUB_CLIENT_ID` | OAuth App client ID |
| `GITHUB_CLIENT_SECRET` | OAuth App client secret, backend only |
| `GITHUB_REDIRECT_URI` | Exact backend callback URL registered with GitHub |
| `TOKEN_ENCRYPTION_KEY` | Stable Fernet key, generated once; secret manager in production |
| `GEMINI_API_KEY` | Backend Gemini credential |
| `FRONTEND_URL` | Exact frontend origin, default `http://localhost:5173` |
| `GITHUB_PRIVATE_REPOS` | `true` explicitly requests `repo` scope; default `false` |
| `COOKIE_SECURE` | `true` for production HTTPS; `false` only for local HTTP |
| `AUTH_DB_PATH` | Optional SQLite path, default `backend/data/auth.sqlite3` |
| `GEMINI_MODEL` | Optional, default `gemini-2.5-flash` |
| `VITE_API_BASE_URL` | Frontend public backend URL, no secret |

Register an OAuth App, not a personal access token. For local development set
the Homepage URL to `http://localhost:5173` and callback to
`http://localhost:8000/auth/github/callback`. Use a separate OAuth App and HTTPS
URLs in production. OAuth App grants may retain previously approved scopes;
changing the private-repository flag does not itself revoke an old grant.

Public GitHub identity and public repository reads require no extra scope.
Private repositories require `repo`, a broad GitHub OAuth App permission which
also grants writes. The application offers only reads, but the credential itself
is broader. Prefer a GitHub App if deployment policy requires fine-grained,
repository-selected, read-only credentials. Organization approvals and SSO can
restrict access even with `repo` consent.

## Live verification with two GitHub users

Run the backend/frontend as described in the root README. Use a normal browser
profile for A and a separate profile/incognito session for B.

1. Enable private repository access if testing private code. Connect GitHub as A.
   Verify the displayed login and repository picker belong to A.
2. Choose an A-only private repository. Index it and ask a RAG question about a
   known function. Ask Live MCP for recent commits and verify the SHAs in GitHub.
3. Ask RAG + MCP what recently changed in that function. Verify both indexed
   code and actual MCP tool steps contribute. Missing/stale indexed evidence
   should be described as such, not presented as current code.
4. Connect B in the other browser. B must see B's accessible GitHub repositories
   and an empty local index list until B indexes something.
5. In B's session, attempt A's private repository in live MCP, ingestion, and RAG.
   GitHub access must fail. Attempt deletion of A's index: it must not delete A's
   chunks. If both users can read a shared repository, they still have separate
   local indexes and use different GitHub credentials.
6. Open `/auth/github/callback?state=invalid&code=invalid`. Expect a safe error
   redirect with no session created. Cancel authorization and replay a completed
   callback: neither should authenticate. The automated tests also cover an
   expired state and using A's state in B's browser.
7. Inspect backend-origin browser responses/storage: the session cookie is
   HttpOnly, `/auth/me` contains only safe metadata and CSRF, and no GitHub access
   token appears in response bodies, localStorage, or sessionStorage. GitHub's
   callback URL contains a short-lived code, which is exchanged only server-side.
8. Restart the backend with the same encryption key and database. Both existing
   sessions should still work until their seven-day expiry; each must still use
   the correct account. Re-login maps back to the same internal identity.
9. Disconnect A. A's saved authorization must no longer work. B must remain
   connected. If GitHub revocation fails, the UI reports that local disconnection
   succeeded and instructs removal in GitHub Settings → Applications.
10. Revoke the OAuth App directly on GitHub. The next protected GitHub access
    should return a reconnect instruction. Test sign-out and rejection of
    POST/DELETE requests with missing/wrong CSRF tokens or another Origin.

Automated tests use mocked provider accounts/tokens and temporary SQLite. They
cover state/PKCE, cross-user repository credentials, encrypted storage, restart,
CSRF, account binding, revocation, user-scoped RAG filters, provider error
redaction, Gemini tool orchestration, and MCP initialization/discovery/calls via
the SDK's in-memory transport. They do not establish a live GitHub OAuth grant
or prove access to GitHub's hosted server from your production network.

## Production considerations and limits

- Deploy with HTTPS and `COOKIE_SECURE=true`; frontend/backend must be same-site
  for the Lax cookie policy. A same-origin reverse proxy is preferred. Configure
  exact CORS origins. Do not expose SQLite/Chroma directories through a web server.
- SQLite is the minimal persistent store for this existing architecture. Use a
  managed relational database and migrations before distributing API instances
  across machines. Back up auth and Chroma data, and test restoration.
- Supply the encryption key through a secret manager with restricted backend
  access. Database encryption protects tokens in a database-only leak; it does
  not protect against compromise of the application and its key together.
  Key rotation requires decrypting/re-encrypting stored tokens; losing the key
  requires users to reconnect. Never regenerate it automatically at startup.
- Restrict database/backups/filesystem permissions. Private code also exists in
  Chroma and temporary source archives; use encrypted disks and retention rules.
  Retrieved code and selected live tool output are sent to the configured Gemini
  provider. Confirm that this is permitted for your users and organizations.
- OAuth App tokens normally do not include refresh tokens. Returned expiry is
  stored and enforced; expired/revoked authorization requires reconnection.
  Disconnect removes local credentials even when GitHub is unreachable; it keeps
  the identity and local index so reconnecting the same account remains stable.
  It does not cancel a provider operation already in flight.
- Application access logs strip OAuth query parameters. Configure reverse proxy,
  APM and error collectors to omit callback queries, cookies, Authorization
  headers and HTTP bodies. Do not enable SDK HTTP/wire debug logging.
- Add deployment rate limits and per-user usage quotas before public signup.
  Ingestion remains synchronous with bounded archive/source sizes; configure
  request deadlines and concurrency for available CPU/memory. A durable worker
  queue is a future step; Redis was deliberately not added.
- Concurrent ingestion of the same user/repository is not coordinated across
  requests yet. Avoid duplicate concurrent indexing; add durable job uniqueness
  and atomic index publication when introducing background workers. A process
  crash during batch writes may require deleting/re-indexing that user's repo.
- The remote MCP server and Gemini require network access. MCP has an explicit
  tool/iteration/deadline limit; unsupported tools and writes are rejected.
  There is no REST fallback masquerading as MCP after a transport failure.
- Users explicitly choose RAG, Live MCP, or RAG + MCP. Combined mode uses available
  indexed code and allows Gemini to choose live reads. Automatic mode routing,
  incremental index refresh and background ingestion are not implemented here.
- Existing tracked binary/cache/database artifacts predate this change. Ignore
  rules do not untrack committed files. Audit and remove such artifacts from
  version control separately; no history rewriting or secret rotation was done.

## Changed-file map

| Files | Change |
| --- | --- |
| `backend/config.py`, `.env.example`, `requirements.txt` | OAuth, encryption/session storage settings, MCP and crypto dependencies |
| `backend/services/auth_store.py` | User/connection/session/state persistence, encryption, expiry and identity binding |
| `backend/routes/auth.py` | OAuth, session status, repository listing, CSRF, logout and disconnect |
| `backend/services/github_api.py` | Per-user REST account administration and repository authorization |
| `backend/services/github_mcp.py` | Official MCP transport, per-request sessions, read-only repository-scoped tools |
| `backend/services/repository_download.py` | Authorized bounded source archives; no credentials in clone configuration |
| `backend/services/vector_db.py`, `search.py` | Mandatory user filters, canonical repository IDs, stable Chroma path |
| `backend/services/llm_service.py` | Actual MCP tool loop, hybrid evidence, safe errors, Pydantic history fix |
| `backend/routes/api.py`, `models/schemas.py` | Authenticated existing routes, private ingestion, hybrid mode and input bounds |
| `backend/main.py` | Auth initialization/routes, exact CORS, response privacy headers and OAuth log filtering |
| `frontend/src/api.js` | Credentialed requests and in-memory application CSRF token |
| `frontend/src/App.jsx`, `components/GitHubConnection.jsx` | Session-aware workspace, repository picker and connection lifecycle |
| `frontend/src/components/Navbar.jsx`, `IngestPanel.jsx`, `QueryPanel.jsx` | Branding, login/logout, selected repository, hybrid mode |
| `frontend/src/components/LandingPage.jsx`, `frontend/index.html`, `frontend/package.json` | GiTMCP product positioning |
| `.gitignore`, `frontend/.env.example`, `README.md`, `frontend/README.md`, this guide | Secret/data exclusions, setup, migration and verification instructions |
| `backend/tests/test_auth.py`, `test_mcp.py`, `test_llm.py`, `test_routes.py`, `test_performance_paths.py` | Security, protocol, orchestration and RAG regression coverage |

## Primary references

- [GitHub OAuth authorization](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/authorizing-oauth-apps)
- [GitHub OAuth scopes](https://docs.github.com/en/apps/oauth-apps/building-oauth-apps/scopes-for-oauth-apps)
- [Official GitHub MCP host integration](https://github.com/github/github-mcp-server/blob/main/docs/host-integration.md)
- [Official remote MCP configuration](https://github.com/github/github-mcp-server/blob/main/docs/remote-server.md)
- [Official MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
