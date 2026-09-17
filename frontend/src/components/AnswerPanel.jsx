import { useState, useEffect } from "react";
import ReactMarkdown from "react-markdown";
import remarkGfm from "remark-gfm";
import { getRepoColor } from "../utils";
import {
  Sparkles, Copy, Check, Terminal,
  FileCode, Layers, Zap, CheckCircle2, GitBranch, Clock,
} from "lucide-react";

/* Format seconds → "12s" or "1m 5s" */
function fmtSecs(s) {
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}

/* Simple 1-second tick hook */
function useElapsedSecs(active) {
  const [secs, setSecs] = useState(0);
  useEffect(() => {
    if (!active) { setSecs(0); return; }
    setSecs(0);
    const id = setInterval(() => setSecs((s) => s + 1), 1000);
    return () => clearInterval(id);
  }, [active]);
  return secs;
}

/* ── Loading Skeleton ─────────────────────────────────────────── */
function SkeletonAnswer() {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {[88, 72, 94, 52, 78, 66, 55].map((w, i) => (
        <div
          key={i}
          className="skeleton-bar"
          style={{
            width:  `${w}%`,
            height: 13,
            borderRadius: 5,
            animationDelay: `${i * 0.09}s`,
          }}
        />
      ))}
    </div>
  );
}

/* ── MCP Step Row (timeline) ──────────────────────────────────── */
function StepRow({ step, index, total }) {
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
      <div style={{ display: "flex", flexDirection: "column", alignItems: "center", flexShrink: 0, paddingTop: 2 }}>
        <div style={{
          width: 18, height: 18, borderRadius: "50%",
          background: "#dafbe1",
          border: "1px solid #4ac26b",
          display: "grid", placeItems: "center",
        }}>
          <CheckCircle2 size={11} color="#1a7f37" />
        </div>
        {index < total - 1 && (
          <div style={{ width: 1, height: 14, background: "#cbd5e1", marginTop: 2 }} />
        )}
      </div>
      <p style={{
        fontSize: "0.8rem", color: "var(--text-secondary)",
        lineHeight: 1.5, paddingTop: 1,
        marginBottom: index < total - 1 ? 6 : 0,
      }}>
        {step}
      </p>
    </div>
  );
}

/* ── Source File Pill ─────────────────────────────────────────── */
function FilePill({ label, color, icon: Icon }) {
  return (
    <div style={{
      display: "inline-flex", alignItems: "center", gap: 5,
      padding: "3px 9px", borderRadius: 99,
      background: "#f1f5f9",
      border: "1px solid var(--border-subtle)",
      fontSize: "0.74rem", color: "var(--text-primary)",
      fontFamily: "JetBrains Mono, monospace",
      whiteSpace: "nowrap", flexShrink: 0,
    }}>
      {color && <span style={{ width: 6, height: 6, borderRadius: "50%", background: color, flexShrink: 0 }} />}
      {Icon  && <Icon size={11} style={{ color: "#2da44e", flexShrink: 0 }} />}
      {label}
    </div>
  );
}


/* ── AnswerPanel ──────────────────────────────────────────────── */
export default function AnswerPanel({ answer, loading, connectedRepo, history = [] }) {
  const [copied, setCopied] = useState(false);


  function handleCopy() {
    if (!answer?.answer) return;
    navigator.clipboard.writeText(answer.answer);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  }

  /* Live elapsed timer while loading */
  const elapsedSecs = useElapsedSecs(loading);

  /* Loading state */
  if (loading) {
    return (
      <div style={{
        borderRadius: 12,
        border: "1px solid var(--border-subtle)",
        background: "#ffffff",
        overflow: "hidden",
        boxShadow: "var(--shadow-card)",
        maxWidth: 760,
      }}>
        <div style={{
          height: 3,
          background: "#2da44e",
        }} />
        <div style={{ padding: "18px 20px" }}>
          <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", gap: 12, marginBottom: 18 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
              <div style={{
                width: 36, height: 36, borderRadius: 8,
                background: "#dafbe1", border: "1px solid #4ac26b",
                display: "grid", placeItems: "center",
              }}>
                <Sparkles size={18} color="#1a7f37" />
              </div>
              <div>
                <div style={{ fontSize: "0.95rem", fontWeight: 700, color: "var(--text-primary)" }}>
                  Synthesizing answer…
                </div>
                <div style={{ fontSize: "0.76rem", color: "var(--text-tertiary)", marginTop: 2 }}>
                  Searching repository {connectedRepo ? `(${connectedRepo})` : ""}
                </div>
              </div>
            </div>
            {elapsedSecs >= 1 && (
              <div style={{
                display: "flex", alignItems: "center", gap: 5,
                padding: "3px 10px", borderRadius: 99,
                background: "#dafbe1",
                border: "1px solid #4ac26b",
                fontSize: "0.73rem", color: "#1a7f37", fontWeight: 600,
                flexShrink: 0,
              }}>
                <Clock size={11} />
                {fmtSecs(elapsedSecs)}
              </div>
            )}
          </div>
          <SkeletonAnswer />
        </div>
      </div>
    );
  }


  if (!answer) return null;

  const isMcp          = answer.mode === "github_mcp";
  const repoNames      = answer.repo_names || (answer.repo_name ? [answer.repo_name] : []);
  const sources        = answer.sources    || [];
  const steps          = answer.steps      || [];
  const fileSample     = answer.file_sample || [];
  const uniqueSources  = sources.filter(
    (s, i, arr) => arr.findIndex((x) => x.path === s.path && x.repo === s.repo) === i
  );

  const accentColor  = isMcp ? "#1a7f37" : "#0969da";
  const accentBg     = isMcp ? "#dafbe1" : "#ddf4ff";
  const accentBorder = isMcp ? "#4ac26b" : "#54aef0";


  return (
    <div
      className="fade-in"
      style={{
        borderRadius: 12,
        border: "1px solid var(--border-subtle)",
        background: "#ffffff",
        overflow: "hidden",
        boxShadow: "var(--shadow-card)",
        maxWidth: 760,
      }}
    >
      {/* Accent top bar */}
      <div style={{
        height: 3,
        background: isMcp ? "#2da44e" : "#0969da",
      }} />

      {/* Header */}
      <div style={{
        padding: "18px 20px 0",
        display: "flex",
        justifyContent: "space-between",
        alignItems: "flex-start",
        gap: 14,
      }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <div style={{
            width: 36, height: 36, borderRadius: 8,
            background: accentBg, border: `1px solid ${accentBorder}`,
            display: "grid", placeItems: "center",
          }}>
            {isMcp ? <Zap size={18} color={accentColor} /> : <Sparkles size={18} color={accentColor} />}
          </div>
          <div>
            <div style={{ fontSize: "0.98rem", fontWeight: 700, color: "var(--text-primary)", lineHeight: 1.2 }}>
              {isMcp ? "Live GitHub MCP Analysis" : "Vector RAG Intelligence"}
            </div>
            <div style={{
              fontSize: "0.78rem", color: "var(--text-tertiary)", marginTop: 3,
              display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap",
            }}>
              {repoNames.map((rn) => (
                <span key={rn} style={{
                  display: "inline-flex", alignItems: "center", gap: 4,
                  padding: "1px 7px", borderRadius: 99,
                  background: "#f1f5f9",
                  border: "1px solid var(--border-subtle)",
                  color: "var(--text-secondary)",
                  fontSize: "0.72rem", fontWeight: 600,
                }}>
                  <GitBranch size={10} />
                  {rn}
                </span>
              ))}
            </div>
          </div>
        </div>

        <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-end", gap: 6, flexShrink: 0 }}>
          <button
            onClick={handleCopy}
            style={{
              display: "flex", alignItems: "center", gap: 5,
              padding: "5px 10px", borderRadius: 6,
              background: "#ffffff",
              border: "1px solid var(--border-medium)",
              color: "var(--text-primary)",
              fontSize: "0.76rem", fontWeight: 600, cursor: "pointer",
              transition: "all 0.14s",
              boxShadow: "0 1px 2px rgba(0,0,0,0.04)",
            }}
          >
            {copied ? <Check size={12} color="#1a7f37" /> : <Copy size={12} />}
            {copied ? "Copied!" : "Copy"}
          </button>
          <div style={{ display: "flex", gap: 6 }}>
            {history && history.length > 0 && (
              <span style={{
                fontSize: "0.7rem", padding: "2px 8px", borderRadius: 99,
                background: "#f1f5f9", border: "1px solid #cbd5e1",
                color: "var(--text-secondary)", fontWeight: 600,
              }}>
                💬 Thread ({history.length / 2 + 0.5})
              </span>
            )}
            <span style={{
              fontSize: "0.7rem", padding: "2px 8px", borderRadius: 99,
              background: accentBg, border: `1px solid ${accentBorder}`,
              color: accentColor, fontWeight: 600,
            }}>
              {isMcp ? "⚡ Live MCP" : `🧩 ${answer.matched_chunks} chunks`}
            </span>
          </div>
        </div>
      </div>

      {/* Question banner */}
      <div style={{
        margin: "14px 20px 0",
        padding: "8px 12px",
        borderRadius: 6,
        background: "#f8fafc",
        border: "1px solid var(--border-subtle)",
        fontSize: "0.84rem",
        color: "var(--text-secondary)",
        fontStyle: "italic",
        lineHeight: 1.5,
      }}>
        "{answer.question}"
      </div>

      {/* MCP Execution Timeline */}
      {steps.length > 0 && (
        <div style={{ margin: "14px 20px 0" }}>
          <div style={{
            display: "flex", alignItems: "center", gap: 6,
            fontSize: "0.7rem", fontWeight: 700, color: "var(--text-tertiary)",
            textTransform: "uppercase", letterSpacing: "0.05em",
            marginBottom: 8,
          }}>
            <Terminal size={12} color={accentColor} />
            MCP Execution Trace
          </div>
          <div style={{
            padding: "10px 12px",
            borderRadius: 6,
            background: "#f8fafc",
            border: "1px solid var(--border-subtle)",
          }}>
            {steps.map((st, i) => (
              <StepRow key={i} step={st} index={i} total={steps.length} />
            ))}
          </div>
        </div>
      )}

      {/* Divider */}
      <div style={{ margin: "16px 20px 0", borderTop: "1px solid var(--border-subtle)" }} />

      {/* Answer body */}
      <div style={{ padding: "16px 20px" }}>
        <div style={{
          fontSize: "0.7rem", fontWeight: 700, color: "var(--text-tertiary)",
          textTransform: "uppercase", letterSpacing: "0.05em",
          display: "flex", alignItems: "center", gap: 5,
          marginBottom: 12,
        }}>
          {isMcp ? <Zap size={12} color={accentColor} /> : <Layers size={12} color={accentColor} />}
          Analysis Result
        </div>
        <div
          className="markdown-body"
          style={{ color: "var(--text-primary)", fontSize: "0.9rem", lineHeight: 1.7 }}
        >
          {answer.answer
            ? <ReactMarkdown remarkPlugins={[remarkGfm]}>{answer.answer}</ReactMarkdown>
            : <span style={{ color: "var(--text-tertiary)", fontStyle: "italic" }}>No answer content returned.</span>
          }
        </div>
      </div>

      {/* Source files */}
      {(uniqueSources.length > 0 || fileSample.length > 0) && (
        <div style={{ margin: "0 20px 20px", borderTop: "1px solid var(--border-subtle)", paddingTop: 14 }}>
          <div style={{
            fontSize: "0.7rem", fontWeight: 700, color: "var(--text-tertiary)",
            textTransform: "uppercase", letterSpacing: "0.05em",
            display: "flex", alignItems: "center", gap: 5,
            marginBottom: 8,
          }}>
            <FileCode size={12} color={accentColor} />
            {isMcp ? "Inspected Repository Files" : "Referenced Source Files"}
          </div>
          <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
            {uniqueSources.map((s, i) => (
              <FilePill key={i} label={`${s.repo}/${s.path}`} color={getRepoColor(s.repo)} />
            ))}
            {fileSample.map((fp, i) => (
              <FilePill key={`fs-${i}`} label={fp} icon={FileCode} />
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
