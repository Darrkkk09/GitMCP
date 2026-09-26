import { useState, useEffect } from "react";
import { api } from "../api";
import {
  CheckCircle,
  LogOut,
  ShieldCheck,
  Zap,
  Lock,
  Loader2,
  AlertCircle,
  ArrowRight,
  GitBranch
} from "lucide-react";

function GithubIcon({ size = 20, className = "" }) {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="2"
      strokeLinecap="round"
      strokeLinejoin="round"
      className={className}
    >
      <path d="M15 22v-4a4.8 4.8 0 0 0-1-3.5c3 0 6-2 6-5.5.08-1.25-.27-2.48-1-3.5.28-1.15.28-2.35 0-3.5 0 0-1 0-3 1.5-2.64-.5-5.36-.5-8 0C6 2 5 2 5 2c-.3 1.15-.3 2.35 0 3.5A5.403 5.403 0 0 0 4 9c0 3.5 3 5.5 6 5.5-.39.49-.68 1.05-.85 1.65-.17.6-.22 1.23-.15 1.85v4" />
      <path d="M9 18c-4.51 2-5-2-7-2" />
    </svg>
  );
}

export default function GitHubConnection({ account, onAccountChange, onSelect, disabled, mode = "auto" }) {
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  const isConnected = !!account?.github_connected;
  const username = account?.user?.github_login || "";
  const avatarUrl = account?.user?.avatar_url || `https://github.com/${username}.png`;

  async function disconnect() {
    setLoading(true);
    setError("");
    try {
      const result = await api.disconnect();
      onSelect("");
      await onAccountChange();
      if (!result.revoked_on_github) {
        setError("Disconnected locally. Revoke access from GitHub Settings → Applications for full removal.");
      }
    } catch (e) {
      setError(e.message);
    } finally {
      setLoading(false);
    }
  }

  // Sidebar Mode (Compact View inside Workspace Sidebar)
  if (mode === "sidebar" || (mode === "auto" && isConnected)) {
    return (
      <section className="sidebar-section">
        <div className="section-label">
          <GithubIcon size={13} />
          GitHub Connection
        </div>

        {isConnected ? (
          <div className="github-connected-card">
            <div className="github-user-info">
              <div className="github-avatar-wrapper">
                <img
                  src={avatarUrl}
                  alt={username}
                  className="github-avatar"
                  onError={(e) => {
                    e.target.onerror = null;
                    e.target.src = "https://github.githubassets.com/images/modules/logos_page/GitHub-Mark.png";
                  }}
                />
                <span className="github-status-dot" title="GitHub Connected via Live MCP" />
              </div>
              <div className="github-user-details">
                <span className="github-username">@{username}</span>
                <span className="github-badge">
                  <CheckCircle size={10} />
                  Connected
                </span>
              </div>
            </div>

            <button
              type="button"
              className="btn btn-secondary btn-sm github-disconnect-btn"
              disabled={loading || disabled}
              onClick={disconnect}
              title="Disconnect GitHub account"
            >
              {loading ? (
                <Loader2 size={13} className="spin" />
              ) : (
                <LogOut size={13} />
              )}
              <span>Disconnect</span>
            </button>
          </div>
        ) : (
          <div className="github-connect-sidebar">
            <p className="github-connect-desc">
              Connect your account to analyze public & private repositories via MCP.
            </p>
            <a href={api.loginUrl} className="btn btn-primary btn-sm btn-full">
              <GithubIcon size={14} />
              Connect GitHub
            </a>
          </div>
        )}

        {error && (
          <div role="alert" className="status-msg error mt-2">
            <AlertCircle size={14} />
            <span>{error}</span>
          </div>
        )}
      </section>
    );
  }

  // Full / Standalone Card Mode (When user is not connected yet in Workspace)
  return (
    <div className="github-connect-container fade-in">
      <div className="github-connect-card">
        {/* Top Icon Badge */}
        <div className="github-card-header-icon">
          <div className="github-icon-badge">
            <GithubIcon size={28} />
          </div>
          <div className="mcp-pulse-ring" />
        </div>

        {/* Header Text */}
        <h2 className="github-card-title">Connect to GitHub</h2>
        <p className="github-card-subtitle">
          Unlock live repository intelligence powered by Model Context Protocol (MCP).
          Analyze codebase architecture, APIs, and routes instantly.
        </p>

        {/* Feature List */}
        <div className="github-features-grid">
          <div className="github-feature-item">
            <div className="github-feature-icon green">
              <Zap size={16} />
            </div>
            <div>
              <h4 className="github-feature-title">Live MCP Analysis</h4>
              <p className="github-feature-desc">
                Inspect repository code directly in real-time without vector embeddings or local cloning.
              </p>
            </div>
          </div>

          <div className="github-feature-item">
            <div className="github-feature-icon blue">
              <ShieldCheck size={16} />
            </div>
            <div>
              <h4 className="github-feature-title">Zero Code Storage</h4>
              <p className="github-feature-desc">
                Your source code is never stored on disk. Context memory budget auto-truncates payloads.
              </p>
            </div>
          </div>

          <div className="github-feature-item">
            <div className="github-feature-icon purple">
              <GitBranch size={16} />
            </div>
            <div>
              <h4 className="github-feature-title">Public & Private Repos</h4>
              <p className="github-feature-desc">
                Seamless access to any repository you own or collaborate on with fine-grained API scope.
              </p>
            </div>
          </div>
        </div>

        {/* Primary CTA */}
        <div className="github-cta-wrapper">
          <a href={api.loginUrl} className="btn btn-primary github-connect-cta">
            <GithubIcon size={18} />
            <span>Connect with GitHub</span>
            <ArrowRight size={16} />
          </a>
        </div>

        {/* Security / Trust Footer */}
        <div className="github-security-note">
          <Lock size={12} />
          <span>Secure OAuth 2.0 authentication. You can revoke access anytime.</span>
        </div>

        {error && (
          <div role="alert" className="status-msg error mt-3">
            <AlertCircle size={14} />
            <span>{error}</span>
          </div>
        )}
      </div>
    </div>
  );
}
