import { useState, useEffect } from "react";
import { Database, GitBranch, Code2, Zap, Server, CheckCircle2 } from "lucide-react";
import { api } from "../api";

/**
 * StatusBar — slim 40px bar showing health indicators + KPI counters.
 * Replaces the old full-width DashboardPanel card.
 */
export default function DashboardPanel({ repos = [] }) {
  const [health, setHealth] = useState({ backend: "checking" });

  useEffect(() => {
    async function checkHealth() {
      try {
        await api.listRepos();
        setHealth({ backend: "healthy" });
      } catch {
        setHealth({ backend: "error" });
      }
    }
    checkHealth();
  }, []);

  const totalRepos   = repos.length;
  const totalChunks  = repos.reduce((acc, r) => acc + (r.chunk_count || 0), 0);
  const totalFiles   = repos.reduce((acc, r) => acc + (r.file_count  || 0), 0);
  const isHealthy    = health.backend === "healthy";
  const isChecking   = health.backend === "checking";

  return (
    <div className="status-bar">
      <div className="status-bar-inner">
        {/* Health indicators */}
        <div className="status-indicators">
          <span className="health-item">
            <Server size={11} />
            Backend
            <span className={`health-dot ${isChecking ? "checking" : isHealthy ? "online" : "offline"}`} />
          </span>
          <span className="health-sep">·</span>
          <span className="health-item">
            <Database size={11} />
            ChromaDB
            <span className="health-dot online" />
          </span>
          <span className="health-sep">·</span>
          <span className="health-item">
            <CheckCircle2 size={11} />
            <span style={{ color: "var(--accent-secondary)", fontWeight: 600 }}>
              gemini-2.0-flash
            </span>
          </span>
          <span className="health-sep">·</span>
          <span className="health-item">
            <Zap size={11} />
            <span style={{ color: "#34d399", fontWeight: 600 }}>MCP Active</span>
          </span>
        </div>

        {/* KPI counters */}
        <div className="kpi-row">
          <span className="kpi-stat">
            <GitBranch size={11} />
            <strong>{totalRepos}</strong> repos
          </span>
          <span className="health-sep">·</span>
          <span className="kpi-stat">
            <Database size={11} />
            <strong>{totalChunks.toLocaleString()}</strong> chunks
          </span>
          <span className="health-sep">·</span>
          <span className="kpi-stat">
            <Code2 size={11} />
            <strong>{totalFiles.toLocaleString()}</strong> files
          </span>
        </div>
      </div>
    </div>
  );
}
