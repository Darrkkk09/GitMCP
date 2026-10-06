import os
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from services import rag_service
from models.schemas import ChatMessage

class RAGTests(unittest.IsolatedAsyncioTestCase):
    def test_sanitize_collection_name(self):
        col = rag_service._sanitize_collection_name("User-123@!", "Darrkkk09", "My.Awesome-Repo!")
        self.assertTrue(col.startswith("rag_"))
        self.assertTrue(col.islower())
        self.assertTrue(all(c.isalnum() or c in ("_", "-") for c in col))

    def test_chunk_repository(self):
        # Create temp folder with dummy code files
        tmp_dir = Path(__file__).resolve().parent / "tmp_rag_test"
        tmp_dir.mkdir(exist_ok=True)
        (tmp_dir / "src").mkdir(exist_ok=True)
        
        file1 = tmp_dir / "src" / "api.py"
        file1.write_text("def hello():\n    return 'world'\n" * 30, encoding="utf-8")
        
        file2 = tmp_dir / "README.md"
        file2.write_text("# Test Repo\nThis is a sample readme file.", encoding="utf-8")
        
        chunks, count = rag_service.chunk_repository(tmp_dir, chunk_size=300, overlap=50)
        self.assertEqual(count, 2)
        self.assertGreater(len(chunks), 0)
        chunk_paths = {c["metadata"]["file_path"] for c in chunks}
        self.assertIn("src/api.py", chunk_paths)
        self.assertIn("README.md", chunk_paths)
        
        # Cleanup
        import shutil
        shutil.rmtree(tmp_dir, ignore_errors=True)

    async def test_answer_question_with_rag_mocked(self):
        # Mock ChromaDB query results & LLM generation
        mock_collection = SimpleNamespace(
            count=lambda: 10,
            query=lambda query_texts, n_results: {
                "documents": [["File: app.py\n```def main(): pass```"]],
                "metadatas": [[{"file_path": "app.py", "start_line": 1, "end_line": 5}]]
            }
        )
        mock_client = SimpleNamespace(
            get_collection=lambda name: mock_collection
        )
        
        mock_llm_response = SimpleNamespace(text="RAG Response: main() handles application startup.")
        
        with patch.object(rag_service, "get_chroma_client", return_value=mock_client), \
             patch.object(rag_service, "_generate_content_with_fallback", AsyncMock(return_value=mock_llm_response)), \
             patch.object(rag_service, "get_rag_status", return_value={"indexed": True, "chunk_count": 10, "repo": "owner/repo"}):
            
            result = await rag_service.answer_question_with_rag(
                user_id="user1",
                owner="owner",
                repo="repo",
                question="What does main() do?",
                history=[ChatMessage(role="user", content="How does app start?")]
            )
            
        self.assertIn("RAG Response:", result["answer"])
        self.assertEqual(len(result["sources"]), 1)
        self.assertEqual(result["sources"][0]["file_path"], "app.py")
        self.assertIn("Mode: Repository RAG (Vector Search)", result["steps"])

if __name__ == "__main__":
    unittest.main()
