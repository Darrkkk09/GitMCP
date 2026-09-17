import { useState, useEffect } from "react";
import { api } from "../api";
import { FolderGit2, Search, Check, Lock, Globe } from "lucide-react";

export default function ReposPanel({ onSelect, selectedRepo, disabled }) {
  const [githubRepos, setGithubRepos] = useState([]);
  const [loading, setLoading] = useState(false);
  const [search, setSearch] = useState("");
  const [customRepo, setCustomRepo] = useState("");

  useEffect(() => {
    let active = true;
    async function fetchRepos() {
      setLoading(true);
      try {
        const data = await api.githubRepos(1);
        if (active) setGithubRepos(data.repositories || []);
      } catch (err) {
        console.error("Failed to fetch accessible GitHub repos:", err);
      } finally {
        if (active) setLoading(false);
      }
    }
    fetchRepos();
    return () => { active = false; };
  }, []);

  const filtered = githubRepos.filter((r) =>
    (r.full_name || "").toLowerCase().includes(search.toLowerCase())
  );

  function handleCustomSubmit(e) {
    e.preventDefault();
    if (customRepo.trim()) {
      onSelect(customRepo.trim());
      setCustomRepo("");
    }
  }

  return (
    <div className="sidebar-section">
      <div className="section-label">
        <FolderGit2 size={13} />
        Select Repository
      </div>

      {/* Manual repo input */}
      <form onSubmit={handleCustomSubmit} style={{ marginBottom: 12 }}>
        <input
          type="text"
          className="input-field"
          placeholder="owner/repo or GitHub URL"
          value={customRepo}
          onChange={(e) => setCustomRepo(e.target.value)}
          disabled={disabled}
          style={{ fontSize: "0.8rem", padding: "6px 10px" }}
        />
      </form>

      {/* Search box for accessible repos */}
      {githubRepos.length > 0 && (
        <div style={{ position: "relative", marginBottom: 8 }}>
          <Search size={12} style={{ position: "absolute", left: 10, top: 10, color: "var(--text-muted)" }} />
          <input
            type="text"
            className="input-field"
            placeholder="Search accessible repos…"
            value={search}
            onChange={(e) => setSearch(e.target.value)}
            disabled={disabled}
            style={{ paddingLeft: 28, fontSize: "0.78rem" }}
          />
        </div>
      )}

      {/* Accessible repo list */}
      {loading ? (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {[1, 2, 3].map((i) => (
            <div key={i} className="skeleton-bar" style={{ height: 32, borderRadius: 6 }} />
          ))}
        </div>
      ) : filtered.length === 0 ? (
        <div className="empty-sidebar" style={{ padding: "12px 8px" }}>
          <p style={{ fontSize: "0.75rem" }}>
            {search ? "No matching repositories found." : "Type an owner/repo above to begin."}
          </p>
        </div>
      ) : (
        <div className="repo-list" style={{ maxHeight: 240, overflowY: "auto" }}>
          {filtered.map((repo) => {
            const isSelected = selectedRepo?.toLowerCase() === (repo.full_name || "").toLowerCase();
            return (
              <div
                key={repo.id || repo.full_name}
                className={`repo-row ${isSelected ? "selected" : ""}`}
                onClick={() => !disabled && onSelect(repo.full_name)}
                style={{ cursor: disabled ? "not-allowed" : "pointer", padding: "6px 10px" }}
              >
                <div style={{ display: "flex", alignItems: "center", gap: 6, flex: 1, minWidth: 0 }}>
                  {repo.private ? <Lock size={12} style={{ opacity: 0.6 }} /> : <Globe size={12} style={{ opacity: 0.6 }} />}
                  <span className="repo-name" style={{ fontSize: "0.8rem", fontWeight: isSelected ? 600 : 400 }}>
                    {repo.full_name}
                  </span>
                </div>
                {isSelected && <Check size={13} style={{ color: "var(--accent-secondary)" }} />}
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
