import { useState, useEffect, useRef } from "react";
import { api } from "../api";
import { Zap, AlertCircle, Send, Clock, GitBranch, Database, RefreshCw, CheckCircle, Layers } from "lucide-react";

function fmtElapsed(ms) {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

export default function QueryPanel({ connectedRepo = "", onAnswer, onLoading, isLoading, history = [], onHistoryChange }) {
  const [question, setQuestion] = useState("");
  const [targetRepo, setTargetRepo] = useState("");
  const [mode, setMode] = useState("mcp"); // "mcp" or "rag"
  const [ragStatus, setRagStatus] = useState({ indexed: false, chunk_count: 0 });
  const [ragLoading, setRagLoading] = useState(false);
  const [error, setError] = useState(null);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => {
    setTargetRepo(connectedRepo);
    if (connectedRepo) {
      checkRagStatus(connectedRepo);
    } else {
      setRagStatus({ indexed: false, chunk_count: 0 });
    }
  }, [connectedRepo]);

  const tickInterval = useRef(null);
  useEffect(() => () => clearInterval(tickInterval.current), []);

  async function checkRagStatus(repo) {
    if (!repo) return;
    try {
      const data = await api.getRAGStatus(repo);
      setRagStatus(data);
    } catch (e) {
      setRagStatus({ indexed: false, chunk_count: 0 });
    }
  }

  async function handleIngestRAG() {
    if (!targetRepo || ragLoading) return;
    setRagLoading(true);
    setError(null);
    try {
      const res = await api.ingestRAG(targetRepo);
      await checkRagStatus(targetRepo);
    } catch (err) {
      setError(err.message || "RAG indexing failed. Check repo access.");
    } finally {
      setRagLoading(false);
    }
  }

  function startTimer() {
    const t0 = Date.now();
    setElapsed(0);
    const id = setInterval(() => setElapsed(Date.now() - t0), 1000);
    tickInterval.current = id;
    return id;
  }

  function stopTimer(id) {
    clearInterval(id);
    tickInterval.current = null;
  }

  const canQuery = targetRepo.trim().length > 0 && question.trim().length > 0 && !isLoading;

  async function handleSubmit(e) {
    if (e) e.preventDefault();
    if (!canQuery) return;
    const currentQuestion = question.trim();
    setError(null);
    onLoading(true);
    const timerId = startTimer();

    try {
      let data;
      if (mode === "rag") {
        data = await api.queryRAG(targetRepo, currentQuestion, history);
      } else {
        data = await api.queryMCP(targetRepo, currentQuestion, history);
      }
      
      console.log(`[${mode.toUpperCase()} UI] API Data:`, data);

      const formatted = {
        ...data,
        answer: data.answer || "The analysis completed but no answer was returned.",
        steps: data.steps || [],
        sources: data.sources || [],
        repo_names: [data.repo_name || targetRepo],
        matched_chunks: mode === "rag" ? (data.sources ? data.sources.length : 0) : "Live MCP",
        mode: mode === "rag" ? "rag" : "github_mcp",
        question: currentQuestion,
      };

      onAnswer(formatted);

      if (onHistoryChange) {
        onHistoryChange([
          ...history.slice(-36),
          { role: "user", content: currentQuestion },
          { role: "assistant", content: formatted.answer },
        ]);
      }

      setQuestion("");
      if (mode === "rag") checkRagStatus(targetRepo);
    } catch (err) {
      console.error(`[${mode.toUpperCase()} UI] Request error:`, err);
      const errMsg = err.message || "Repository analysis failed. Please try again.";
      setError(errMsg);
      onAnswer({
        mode: mode === "rag" ? "rag" : "github_mcp",
        repo_names: [targetRepo],
        question: currentQuestion,
        answer: `**Error processing request:** ${errMsg}`,
        steps: err.steps || ["Query failed"],
        error: true,
      });
    } finally {
      stopTimer(timerId);
      onLoading(false);
    }
  }

  return (
    <div className="query-section" style={{ background: "var(--bg-card)", border: "1px solid var(--border-subtle)", borderRadius: 12, padding: 18 }}>
      
      {/* Pipeline Mode Switcher & Repo Header */}
      <div style={{ display: "flex", flexDirection: "column", gap: 12, marginBottom: 14, paddingBottom: 12, borderBottom: "1px solid var(--border-subtle)" }}>
        
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 10 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <GitBranch size={15} style={{ color: "var(--accent-primary)" }} />
            <span style={{ fontSize: "0.88rem", fontWeight: 700, color: "var(--text-primary)" }}>
              {targetRepo ? targetRepo : "No repository selected"}
            </span>
          </div>

          {/* Mode Selector Segment */}
          <div className="nav-pills" style={{ padding: 2 }}>
            <button
              type="button"
              className={`nav-pill ${mode === "mcp" ? "active" : ""}`}
              onClick={() => setMode("mcp")}
              style={{ padding: "4px 10px", fontSize: "0.76rem" }}
            >
              <Zap size={12} color="#1a7f37" />
              ⚡ Live MCP
            </button>
            <button
              type="button"
              className={`nav-pill ${mode === "rag" ? "active" : ""}`}
              onClick={() => setMode("rag")}
              style={{ padding: "4px 10px", fontSize: "0.76rem" }}
            >
              <Database size={12} color="#0969da" />
              📚 Repo RAG
            </button>
          </div>
        </div>

        {/* Pipeline Info & RAG Controls */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", flexWrap: "wrap", gap: 10, background: "#f8fafc", padding: "8px 12px", borderRadius: 8, border: "1px solid var(--border-subtle)" }}>
          {mode === "mcp" ? (
            <div style={{ fontSize: "0.76rem", color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: 6 }}>
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#10b981", display: "inline-block" }} />
              <strong>Live MCP Mode:</strong> Direct GitHub tool calls without embedding storage.
            </div>
          ) : (
            <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", width: "100%", flexWrap: "wrap", gap: 8 }}>
              <div style={{ fontSize: "0.76rem", color: "var(--text-secondary)", display: "flex", alignItems: "center", gap: 6 }}>
                <span style={{ width: 6, height: 6, borderRadius: "50%", background: ragStatus.indexed ? "#0969da" : "#d97706", display: "inline-block" }} />
                <strong>RAG ChromaDB Vector Mode:</strong> {ragStatus.indexed ? `Indexed (${ragStatus.chunk_count} code chunks)` : "Not indexed yet"}
              </div>

              {targetRepo && (
                <button
                  type="button"
                  className="btn btn-secondary btn-sm"
                  onClick={handleIngestRAG}
                  disabled={ragLoading || isLoading}
                  style={{ fontSize: "0.73rem", padding: "3px 8px", display: "flex", alignItems: "center", gap: 5 }}
                >
                  {ragLoading ? <RefreshCw size={11} className="spin" /> : <Layers size={11} />}
                  <span>{ragStatus.indexed ? "Re-index Repo" : "Index Repo for RAG"}</span>
                </button>
              )}
            </div>
          )}
        </div>

      </div>

      {/* Query Form */}
      <form onSubmit={handleSubmit} className="query-fields">
        <div>
          <div className="field-label" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <span style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-secondary)" }}>
              {history.length > 0 ? "Follow-up Question" : `Ask question using ${mode === "rag" ? "RAG Vector Search" : "Live MCP"}`}
            </span>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              {history.length > 0 && onHistoryChange && (
                <button
                  type="button"
                  onClick={() => { onHistoryChange([]); onAnswer(null); }}
                  style={{ background: "none", border: "none", color: "var(--text-muted)", fontSize: "0.72rem", cursor: "pointer", padding: 0 }}
                >
                  Clear Thread
                </button>
              )}
              <span style={{ fontSize: "0.7rem", color: "var(--text-muted)" }}>Ctrl+Enter to send</span>
            </div>
          </div>

          <textarea
            className="input-field"
            placeholder={
              !targetRepo
                ? "Select a repository from the sidebar or type owner/repo..."
                : history.length > 0
                ? "Ask a follow-up question..."
                : mode === "rag"
                ? "Ask code question via ChromaDB RAG vector search..."
                : "Ask code question via live GitHub MCP tool calls..."
            }
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            disabled={isLoading || !targetRepo}
            rows={3}
            onKeyDown={(e) => {
              if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) handleSubmit(e);
            }}
            style={{ resize: "vertical" }}
          />
        </div>

        <button
          className="btn btn-primary btn-full"
          type="submit"
          disabled={!canQuery}
          style={{ marginTop: 10, display: "flex", alignItems: "center", justifyContent: "center", gap: 8 }}
        >
          {isLoading ? (
            <>
              <div className="spinner" />
              <span>{mode === "rag" ? "Searching ChromaDB & Synthesizing RAG..." : "Inspecting repository via MCP..."}</span>
              {elapsed >= 2 && (
                <span style={{ marginLeft: "auto", fontSize: "0.73rem", opacity: 0.8, display: "flex", alignItems: "center", gap: 4 }}>
                  <Clock size={11} />
                  {fmtElapsed(elapsed)}
                </span>
              )}
            </>
          ) : (
            <>
              <Send size={14} />
              <span>{mode === "rag" ? "Query via RAG" : "Ask GiTMCP"}</span>
            </>
          )}
        </button>
      </form>

      {error && (
        <div className="status-msg error" style={{ marginTop: 12, display: "flex", alignItems: "flex-start", gap: 8 }}>
          <AlertCircle size={14} style={{ flexShrink: 0, marginTop: 2 }} />
          <div>{error}</div>
        </div>
      )}
    </div>
  );
}
