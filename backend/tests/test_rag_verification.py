import os
import sys
import shutil
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import rag_service, llm_service
from models.schemas import ChatMessage

class RAGVerificationTests(unittest.IsolatedAsyncioTestCase):
    
    # Verification Item 1: Existing MCP functionality & normalization
    def test_verif_1_mcp_normalization_intact(self):
        contents = [
            llm_service.types.Content(role="user", parts=[llm_service.types.Part.from_text(text="Query")]),
            llm_service.types.Content(role="tool", parts=[llm_service.types.Part.from_function_response(name="tool_a", response={"content": "result"})])
        ]
        norm = llm_service.normalize_contents_for_model(contents, "gemini-3.6-flash")
        self.assertEqual(len(norm), 2)
        self.assertEqual(norm[1].role, "user")
        self.assertIn("MCP Tool 'tool_a' result:", norm[1].parts[0].text)

    # Verification Item 2: One repository can be cloned -> chunked -> embedded -> stored -> queried
    async def test_verif_2_single_repo_lifecycle(self):
        tmp_dir = Path(__file__).resolve().parent / "tmp_single_repo"
        tmp_dir.mkdir(exist_ok=True)
        (tmp_dir / "routes").mkdir(exist_ok=True)
        (tmp_dir / "routes" / "login.py").write_text("def authenticate_user(): return True", encoding="utf-8")
        
        user_id = "user_v2"
        owner, repo = "org", "single_repo"
        
        # Patch clone to use local tmp directory
        with patch.object(rag_service, "clone_or_update_repo", return_value=tmp_dir):
            ingest_res = rag_service.ingest_repository_rag(user_id, owner, repo)
            self.assertEqual(ingest_res["status"], "success")
            self.assertGreater(ingest_res["chunk_count"], 0)
            
            status = rag_service.get_rag_status(user_id, owner, repo)
            self.assertTrue(status["indexed"])
            self.assertEqual(status["chunk_count"], ingest_res["chunk_count"])

        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Verification Item 3: Multiple repositories can belong to one profile
    async def test_verif_3_multi_repo_single_profile(self):
        user_id = "profile_owner_99"
        
        dir1 = Path(__file__).resolve().parent / "tmp_repo1"
        dir2 = Path(__file__).resolve().parent / "tmp_repo2"
        dir1.mkdir(exist_ok=True)
        dir2.mkdir(exist_ok=True)
        
        (dir1 / "auth.py").write_text("def login(): pass", encoding="utf-8")
        (dir2 / "pay.py").write_text("def process_payment(): pass", encoding="utf-8")
        
        with patch.object(rag_service, "clone_or_update_repo", side_effect=[dir1, dir2]):
            rag_service.ingest_repository_rag(user_id, "org", "auth_repo")
            rag_service.ingest_repository_rag(user_id, "org", "pay_repo")
            
        cols = rag_service.get_user_collections(user_id)
        repo_names = {c["repo"] for c in cols}
        self.assertIn("auth_repo", repo_names)
        self.assertIn("pay_repo", repo_names)
        
        shutil.rmtree(dir1, ignore_errors=True)
        shutil.rmtree(dir2, ignore_errors=True)

    # Verification Item 4: A query can retrieve chunks from multiple repositories
    async def test_verif_4_multi_repo_chunk_retrieval(self):
        user_id = "multi_search_user"
        dir1 = Path(__file__).resolve().parent / "tmp_mrepo1"
        dir2 = Path(__file__).resolve().parent / "tmp_mrepo2"
        dir1.mkdir(exist_ok=True)
        dir2.mkdir(exist_ok=True)
        (dir1 / "user.py").write_text("class UserAccount:\n    def get_id(): pass", encoding="utf-8")
        (dir2 / "billing.py").write_text("class BillingAccount:\n    def get_balance(): pass", encoding="utf-8")
        
        with patch.object(rag_service, "clone_or_update_repo", side_effect=[dir1, dir2]):
            rag_service.ingest_repository_rag(user_id, "company", "user_repo")
            rag_service.ingest_repository_rag(user_id, "company", "billing_repo")
            
        mock_llm_res = SimpleNamespace(text="Retrieved accounts from both user_repo and billing_repo.")
        with patch.object(rag_service, "_generate_content_with_fallback", AsyncMock(return_value=mock_llm_res)):
            res = await rag_service.answer_question_with_rag(
                user_id=user_id,
                owner="company",
                repo="all",
                question="What account classes exist?"
            )
            
        retrieved_repos = {s["repo"] for s in res["sources"]}
        self.assertIn("company/user_repo", retrieved_repos)
        self.assertIn("company/billing_repo", retrieved_repos)
        
        shutil.rmtree(dir1, ignore_errors=True)
        shutil.rmtree(dir2, ignore_errors=True)

    # Verification Item 5: Follow-up questions retain context like "that function"
    async def test_verif_5_followup_question_context(self):
        user_id = "history_user"
        tmp_dir = Path(__file__).resolve().parent / "tmp_hist_repo"
        tmp_dir.mkdir(exist_ok=True)
        (tmp_dir / "cart.py").write_text("def checkout(): return 'paid'", encoding="utf-8")
        
        with patch.object(rag_service, "clone_or_update_repo", return_value=tmp_dir):
            rag_service.ingest_repository_rag(user_id, "store", "cart_repo")
            
        history = [
            ChatMessage(role="user", content="Explain the checkout function in cart.py"),
            ChatMessage(role="assistant", content="The checkout function returns 'paid'.")
        ]
        
        captured_contents = []
        async def mock_generate(contents, config, reason="rag_query", call_count=1):
            captured_contents.append(contents)
            return SimpleNamespace(text="Followup answer explaining that function")
            
        with patch.object(rag_service, "_generate_content_with_fallback", side_effect=mock_generate):
            res = await rag_service.answer_question_with_rag(
                user_id=user_id,
                owner="store",
                repo="cart_repo",
                question="What does that function return?",
                history=history
            )
            
        self.assertGreater(len(captured_contents), 0)
        sent_text = str(captured_contents[0])
        self.assertIn("Explain the checkout function in cart.py", sent_text)
        self.assertIn("What does that function return?", sent_text)
        self.assertIn("Followup answer explaining that function", res["answer"])
        
        shutil.rmtree(tmp_dir, ignore_errors=True)

    # Verification Item 6: Frontend can switch MCP <-> RAG without affecting MCP flow
    async def test_verif_6_independent_mcp_rag_routes(self):
        from routes import api as api_routes
        
        req_mcp = api_routes.MCPQueryRequest(github_repo="owner/repo", question="MCP question", mode="mcp")
        req_rag = api_routes.MCPQueryRequest(github_repo="owner/repo", question="RAG question", mode="rag")
        
        with patch.object(api_routes, "query_repository_mcp", AsyncMock(return_value={"mode": "github_mcp"})) as mock_mcp, \
             patch.object(api_routes, "query_repository_rag", AsyncMock(return_value={"mode": "rag"})) as mock_rag, \
             patch.object(api_routes, "authorized_repo", return_value="owner/repo"):
            
            res1 = await api_routes.unified_chat(req_mcp, user_id="user1")
            res2 = await api_routes.unified_chat(req_rag, user_id="user1")
            
            self.assertEqual(res1["mode"], "github_mcp")
            self.assertEqual(res2["mode"], "rag")
            mock_mcp.assert_awaited_once()
            mock_rag.assert_awaited_once()

if __name__ == "__main__":
    unittest.main()
