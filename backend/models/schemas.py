from pydantic import BaseModel, Field
from typing import List, Optional, Literal


class ChatMessage(BaseModel):
    role: Literal["user", "assistant"]
    content: str = Field(max_length=24000)


class MCPQueryRequest(BaseModel):
    github_repo: str  # Can be 'owner/repo' or full GitHub URL
    question: str = Field(min_length=1, max_length=12000)
    history: List[ChatMessage] = Field(default_factory=list, max_length=40)
    mode: Optional[str] = "mcp"

class RAGIngestRequest(BaseModel):
    github_repo: str

# Alias for clean architecture
ChatRequest = MCPQueryRequest
