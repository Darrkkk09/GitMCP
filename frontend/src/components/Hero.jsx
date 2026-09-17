import { Sparkles } from "lucide-react";

export default function Hero() {
  return (
    <section className="hero fade-in">
      <div className="hero-pill">
        <Sparkles size={14} style={{ color: "var(--accent-secondary)" }} />
        <span>Gemini AI + ChromaDB Vector RAG</span>
      </div>

      <h1 className="hero-title">
        Instant Intelligence for <br />
        <span className="gradient-text">Any GitHub Repository</span>
      </h1>

      <p className="hero-subtitle">
        Index codebases into local vector storage for multi-repository semantic search,
        or query live GitHub repositories on-the-fly via GitHub MCP function calling.
      </p>
    </section>
  );
}
