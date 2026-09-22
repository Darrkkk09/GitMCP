import { useState, useEffect, useRef } from "react";
import { api } from "../api";
import { Zap, AlertCircle, Send, Clock, GitBranch } from "lucide-react";

function fmtElapsed(ms) {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

export default function QueryPanel({ connectedRepo = "", onAnswer, onLoading, isLoading, history = [], onHistoryChange }) {
  const [question, setQuestion] = useState("");
  const [mcpRepo, setMcpRepo] = useState("");
  const [error, setError] = useState(null);
  const [elapsed, setElapsed] = useState(0);

  useEffect(() => { setMcpRepo(connectedRepo); }, [connectedRepo]);

  const tickInterval = useRef(null);
  useEffect(() => () => clearInterval(tickInterval.current), []);

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

  const canQuery = mcpRepo.trim().length > 0 && question.trim().length > 0 && !isLoading;

  async function handleSubmit(e) {
    if (e) e.preventDefault();
    if (!canQuery) return;
    const currentQuestion = question.trim();
    setError(null);
    onLoading(true);
    const timerId = startTimer();

    try {
      const data = await api.queryMCP(mcpRepo, currentQuestion, history);
      
      console.log("[MCP UI] Raw API Data:", data);
      console.log("[MCP UI] Answer length:", data?.answer ? data.answer.length : 0);
      console.log("[MCP UI] Trace entries:", data?.steps ? data.steps.length : 0);

      const formatted = {
        ...data,
        answer: data.answer || "The analysis completed but no answer was returned.",
        steps: data.steps || [],
        repo_names: [data.repo_name || mcpRepo],
        matched_chunks: "Live MCP",
        mode: "github_mcp",
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
    } catch (err) {
      console.error("[MCP UI] Request error:", err);
      const errMsg = err.message || "GitHub repository could not be accessed. Verify permission or reconnect GitHub.";
      setError(errMsg);
      onAnswer({
        mode: "github_mcp",
        repo_names: [mcpRepo],
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
      {/* Target Repo Bar */}
      <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 14, paddingBottom: 10, borderBottom: "1px solid var(--border-subtle)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          <GitBranch size={14} style={{ color: "var(--accent-primary)" }} />
          <span style={{ fontSize: "0.85rem", fontWeight: 600, color: "var(--text-primary)" }}>
            {mcpRepo ? mcpRepo : "No repository selected"}
          </span>
        </div>
        {mcpRepo && (
          <span style={{ display: "inline-flex", alignItems: "center", gap: 5, padding: "3px 9px", borderRadius: "9999px", background: "#10b98118", border: "1px solid #10b98135", color: "#10b981", fontSize: "0.72rem", fontWeight: 600 }}>
            <span style={{ width: 5, height: 5, borderRadius: "50%", background: "#10b981" }} />
            Live GitHub MCP
          </span>
        )}
      </div>

      <form onSubmit={handleSubmit} className="query-fields">
        <div>
          <div className="field-label" style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 8 }}>
            <span style={{ fontSize: "0.8rem", fontWeight: 600, color: "var(--text-secondary)" }}>
              {history.length > 0 ? "Follow-up Question" : "Ask anything about this repository"}
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
              !mcpRepo
                ? "Select a repository from the sidebar or type owner/repo..."
                : history.length > 0
                ? "Ask a follow-up question..."
                : "e.g. What APIs does this repo have? Or give me a high-level architecture overview."
            }
            value={question}
            onChange={(e) => setQuestion(e.target.value)}
            disabled={isLoading || !mcpRepo}
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
              <span>↻ Inspecting repository...</span>
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
              <span>Ask GiTMCP</span>
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
