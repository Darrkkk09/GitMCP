import {
  Sparkles, ArrowRight, Zap, Database,
  ShieldCheck, Terminal, Layers,
} from "lucide-react";

const features = [
  {
    icon:  Zap,
    color: "#1a7f37",
    bg:    "#dafbe1",
    title: "Live GitHub MCP",
    desc:  "Connect your GitHub account to read authorized repositories, commits, issues, pull requests, and CI information through the official GitHub MCP Server.",
  },
  {
    icon:  Database,
    color: "#0969da",
    bg:    "#ddf4ff",
    title: "ChromaDB Vector RAG",
    desc:  "Index codebases into a persistent vector database using BAAI/bge-small-en-v1.5 embeddings for instant offline retrieval.",
  },
  {
    icon:  Layers,
    color: "#8250df",
    bg:    "#fbefff",
    title: "Multi-Repo Synthesis",
    desc:  "Select multiple indexed repos simultaneously to trace cross-project dependencies, API contracts, and shared architectures.",
  },
  {
    icon:  ShieldCheck,
    color: "#bf8700",
    bg:    "#fff8c5",
    title: "Zero-Hallucination",
    desc:  "Gemini 2.5 Flash synthesizes answers strictly grounded in retrieved code with exact source file path attributions.",
  },
];

const steps = [
  {
    num:   "01",
    title: "Select or Ingest",
    desc:  "Paste a GitHub URL to index a codebase locally, or type any repo name for live MCP queries without cloning.",
  },
  {
    num:   "02",
    title: "Ask Your Question",
    desc:  "Query architecture, specific functions, bugs, commits, or pull requests — all in natural language.",
  },
  {
    num:    "03",
    title:  "Get Grounded Answer",
    desc:   "Receive structured markdown with code blocks and exact source file citations. No hallucinations.",
    accent: true,
  },
];

export default function LandingPage({ onLaunchWorkspace, onLaunchMcp }) {
  return (
    <div className="landing fade-in">

      {/* ── Hero ─────────────────────────────────────────────── */}
      <section className="landing-hero">
        <div className="hero-pill">
          <Sparkles size={13} style={{ color: "#2da44e" }} />
          GiTMCP · AI-powered GitHub Repository Intelligence
        </div>

        <h1 className="hero-title">
          Speak to Any{" "}
          <span className="gradient-text">GitHub Repository</span>
          <br />In Real-Time
        </h1>

        <p className="hero-subtitle">
          Combine local <strong>ChromaDB Vector RAG</strong> for deep codebases with{" "}
          <strong>GitHub Model Context Protocol (MCP)</strong> to chat with any repository
          on-the-fly without cloning.
        </p>

        <div className="hero-actions">
          <button
            onClick={onLaunchWorkspace}
            className="btn btn-primary"
            style={{ padding: "11px 24px", fontSize: "0.9rem" }}
          >
            Launch Workspace
            <ArrowRight size={15} />
          </button>
          <button
            onClick={onLaunchMcp}
            className="btn btn-secondary"
            style={{ padding: "11px 22px", fontSize: "0.9rem" }}
          >
            ⚡ Live GitHub MCP
          </button>
        </div>
      </section>

      {/* ── Features ─────────────────────────────────────────── */}
      <section className="landing-section">
        <div className="landing-section-header">
          <h2>Engineered for Modern Developers</h2>
          <p>Dual-engine intelligence powered by local embeddings &amp; live GitHub APIs</p>
        </div>

        <div className="features-grid">
          {features.map((f) => (
            <div key={f.title} className="feature-item">
              <div
                className="feature-icon"
                style={{ background: f.bg, color: f.color }}
              >
                <f.icon size={19} />
              </div>
              <h3>{f.title}</h3>
              <p>{f.desc}</p>
            </div>
          ))}
        </div>
      </section>

      {/* ── How It Works ─────────────────────────────────────── */}
      <section className="landing-section">
        <div className="landing-section-header">
          <div style={{
            display: "inline-flex", alignItems: "center", gap: 6,
            color: "#1a7f37", fontSize: "0.75rem", fontWeight: 700,
            textTransform: "uppercase", letterSpacing: "0.05em",
            marginBottom: 10,
          }}>
            <Terminal size={13} /> How It Works
          </div>
          <h2>Three Steps to Code Intelligence</h2>
        </div>

        <div className="steps-row">
          {steps.map((s) => (
            <div key={s.num} className="step-item">
              <div
                className="step-num"
                style={s.accent ? { color: "#2da44e" } : { color: "#64748b" }}
              >
                {s.num}
              </div>
              <div
                className="step-title"
                style={s.accent ? { color: "#1a7f37" } : {}}
              >
                {s.title}
              </div>
              <div className="step-desc">{s.desc}</div>
            </div>
          ))}
        </div>

        <div style={{ textAlign: "center", marginTop: 40 }}>
          <button
            onClick={onLaunchWorkspace}
            className="btn btn-primary"
            style={{ padding: "11px 28px", fontSize: "0.88rem" }}
          >
            Get Started
          </button>
        </div>
      </section>

    </div>
  );
}
