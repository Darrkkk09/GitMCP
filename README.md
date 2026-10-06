# GiTMCP - Hybrid RAG + Live GitHub MCP Intelligence

**AI-powered GitHub Repository Intelligence with Hybrid RAG & Live MCP Support**

GiTMCP combines **Live GitHub MCP Tool Calling** with **Repository Vector RAG (ChromaDB)** to provide instant, deep repository analysis and code understanding.

---

## 🌟 Key Features

- **⚡ Live GitHub MCP Mode**: Directly inspects live GitHub repositories in real time using official GitHub Model Context Protocol (MCP) tools—no local code storage required.
- **📚 Repository RAG Mode (ChromaDB)**: Clones, chunks, embeds, and indexes repositories locally in a persistent ChromaDB vector store. Supports fast similarity search and multi-repository queries.
- **🌐 Multi-Repository Profile Support**: Single user profile can index and search across multiple public & private repositories simultaneously.
- **💬 Follow-up Question Support**: Conversation thread history is automatically preserved across both MCP and RAG modes to resolve context like *"that function"* or *"previous component"*.
- **🤖 Robust LLM Fallback Chain**: Multi-key Gemini 2.5/3.6 Flash with automatic Groq (Llama 3.3/3.1) fallback and tool role normalization.
- **🔒 Secure OAuth 2.0 Auth**: GitHub OAuth authentication with CSRF protection, Fernet-encrypted token storage, and session security.

---

## 🏗️ Architecture & Pipelines

```
                           ┌───────────────────────────┐
                           │   React/Vite Frontend     │
                           └─────────────┬─────────────┘
                                         │
                         ┌───────────────┴───────────────┐
                         │      FastAPI Backend          │
                         └───────┬───────────────┬───────┘
                                 │               │
            ┌────────────────────┴──┐         ┌──┴────────────────────┐
            │   Live MCP Pipeline   │         │    RAG Pipeline       │
            └──────────┬────────────┘         └──────────┬────────────┘
                       │                                 │
           GitHub MCP Server                ChromaDB Vector Store
        (Live Repository Tools)             (Cloned & Chunked Repos)
```

1. **Live MCP Pipeline (`/query/mcp`)**:
   - Initializes a streamable session with the GitHub MCP server for real-time tool execution (`get_file_contents`, `list_commits`, `search_code`, etc.).
   - Governed by Context Budgeting & strict uninspected path guards to prevent hallucination.

2. **RAG Pipeline (`/query/rag` & `/rag/ingest`)**:
   - Clones repository into `backend/data/cloned_repos/{user_id}/{owner}_{repo}`.
   - Splits code files into overlapping text chunks with line number metadata.
   - Stores embeddings into ChromaDB collection `rag_{user_id}_{owner}_{repo}`.
   - Supports multi-repo queries when specifying comma-separated repos or `repo="all"`.

---

## 🚀 Getting Started

### Prerequisites

- Python 3.11+
- Node.js 18+

### 1. Configure Environment Variables

Create `backend/.env`:

```env
GEMINI_API_KEY=your_gemini_api_key
GROQ_API_KEY=your_groq_api_key
GITHUB_CLIENT_ID=your_github_oauth_client_id
GITHUB_CLIENT_SECRET=your_github_oauth_client_secret
GITHUB_REDIRECT_URI=http://localhost:8000/auth/github/callback
TOKEN_ENCRYPTION_KEY=your_fernet_encryption_key
```

### 2. Backend Setup

```powershell
cd backend
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python main.py
```
*Backend runs on `http://localhost:8000`.*

### 3. Frontend Setup

```powershell
cd frontend
npm install
npm run dev
```
*Frontend runs on `http://localhost:5173`.*

---

## 📡 API Endpoints

| Endpoint | Method | Description |
| :--- | :--- | :--- |
| `/auth/github` | `GET` | Initiates GitHub OAuth flow |
| `/auth/me` | `GET` | User session & CSRF token |
| `/query/mcp` | `POST` | Execute Live GitHub MCP tool query |
| `/query/rag` | `POST` | Execute RAG vector similarity search query |
| `/chat` | `POST` | Unified endpoint (`mode: "mcp"` or `"rag"`) |
| `/rag/ingest` | `POST` | Clone, chunk, and index repository into ChromaDB |
| `/rag/status` | `GET` | Check ChromaDB index status and chunk count |

---

## 🧪 Verification & Testing

Run the full pytest suite (34 unit & integration tests covering MCP, RAG, multi-repo, and follow-ups):

```powershell
cd backend
python -m pytest tests/test_llm.py tests/test_rag.py tests/test_rag_verification.py
```

Frontend build check:

```powershell
cd frontend
npm run build
```

---

## 📄 License

MIT License. Designed with Antigravity AI Code Intelligence.
