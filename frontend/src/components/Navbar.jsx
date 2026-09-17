import { Zap, ExternalLink, LayoutDashboard, Home } from "lucide-react";
import { api, API_BASE } from "../api";

export default function Navbar({ activeTab, onTabChange, account, onLogout, busy }) {
  return (
    <nav className="navbar">
      <div className="navbar-inner">
        <a
          className="navbar-brand"
          href="#"
          onClick={(e) => { e.preventDefault(); onTabChange("landing"); }}
        >
          <div className="navbar-logo-icon">
            <Zap size={15} fill="currentColor" />
          </div>
          <span className="navbar-name">GiTMCP</span>
        </a>

        <div className="nav-pills">
          <button
            type="button"
            className={`nav-pill ${activeTab === "landing" ? "active" : ""}`}
            onClick={() => onTabChange("landing")}
          >
            <Home size={13} />
            Home
          </button>
          <button
            type="button"
            className={`nav-pill ${activeTab === "workspace" ? "active" : ""}`}
            onClick={() => onTabChange("workspace")}
          >
            <LayoutDashboard size={13} />
            Workspace
          </button>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          {account ? (
            <button className="btn btn-secondary btn-sm" disabled={busy} onClick={onLogout}>
              Sign out ({account.user.github_login})
            </button>
          ) : (
            <a className="btn btn-primary btn-sm" href={api.loginUrl}>
              Connect GitHub
            </a>
          )}
          {account?.github_connected && (
            <span
              className="navbar-badge"
              style={{
                display: "inline-flex",
                alignItems: "center",
                gap: 6,
                padding: "4px 10px",
                borderRadius: "9999px",
                background: "#10b98118",
                border: "1px solid #10b98135",
                color: "#10b981",
                fontSize: "0.75rem",
                fontWeight: 600,
              }}
            >
              <span style={{ width: 6, height: 6, borderRadius: "50%", background: "#10b981", display: "inline-block" }} />
              Live GitHub MCP
            </span>
          )}
          <a
            href={`${API_BASE}/docs`}
            target="_blank"
            rel="noreferrer"
            className="btn btn-secondary btn-sm"
            style={{ textDecoration: "none" }}
          >
            API Docs
            <ExternalLink size={12} style={{ opacity: 0.65 }} />
          </a>
        </div>
      </div>
    </nav>
  );
}
