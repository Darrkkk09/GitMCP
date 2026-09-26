import { useEffect, useState, useCallback, useRef } from "react";
import "./index.css";
import { api } from "./api";
import Navbar from "./components/Navbar";
import LandingPage from "./components/LandingPage";
import ReposPanel from "./components/ReposPanel";
import QueryPanel from "./components/QueryPanel";
import AnswerPanel from "./components/AnswerPanel";
import GitHubConnection from "./components/GitHubConnection";
import { Bot } from "lucide-react";

export default function App() {
  const [account, setAccount] = useState(null);
  const [authLoading, setAuthLoading] = useState(true);
  const [authError, setAuthError] = useState("");
  const [connectedRepo, setConnectedRepo] = useState("");
  const [activeTab, setActiveTab] = useState("landing");
  const [answer, setAnswer] = useState(null);
  const [answerLoading, setAnswerLoading] = useState(false);
  const [history, setHistory] = useState([]);

  // Ref to scroll the main panel to the answer when it arrives
  const answerRef = useRef(null);
  const mainPanelRef = useRef(null);

  const refreshAccount = useCallback(async () => {
    try { setAccount(await api.me()); }
    catch { setAccount(null); }
    finally { setAuthLoading(false); }
  }, []);

  useEffect(() => {
    const status = new URLSearchParams(window.location.search).get("auth");
    if (status === "error") setAuthError("GitHub connection failed or was cancelled. Try again; sign out first to use a different account.");
    if (status) {
      window.history.replaceState({}, "", window.location.pathname);
      setActiveTab("workspace");
    }
    refreshAccount();
  }, [refreshAccount]);

  async function logout() {
    try { await api.logout(); setAccount(null); }
    catch (e) { setAuthError(e.message); }
  }

  function selectConnectedRepo(repo) {
    setConnectedRepo(repo); setHistory([]); setAnswer(null);
  }

  // Auto-scroll to answer whenever a new answer arrives
  useEffect(() => {
    if (answer && answerRef.current && mainPanelRef.current) {
      setTimeout(() => {
        answerRef.current?.scrollIntoView({ behavior: "smooth", block: "start" });
      }, 80);
    }
  }, [answer]);

  return (
    <>
      <Navbar
        account={account}
        onLogout={logout}
        busy={answerLoading}
        activeTab={activeTab}
        onTabChange={setActiveTab}
      />

      {authError && <p role="alert" className="status-msg error">{authError}</p>}
      {activeTab === "landing" ? (
        <LandingPage
          onLaunchWorkspace={() => setActiveTab("workspace")}
          onLaunchMcp={() => setActiveTab("workspace")}
        />
      ) : authLoading ? <p>Checking your session…</p> : !account?.github_connected ? (
        <GitHubConnection account={account} onAccountChange={refreshAccount} onSelect={selectConnectedRepo} mode="full" />
      ) : (
        <div className="workspace fade-in">
          <div className="workspace-body">
            {/* Left sidebar */}
            <aside className="sidebar">
              <GitHubConnection account={account} onAccountChange={refreshAccount} onSelect={selectConnectedRepo} disabled={answerLoading} mode="sidebar" />
              <div className="sidebar-divider" />
              <ReposPanel
                selectedRepo={connectedRepo}
                onSelect={selectConnectedRepo}
              />
            </aside>

            {/* Right main panel */}
            <div className="main-panel" ref={mainPanelRef}>
              <div ref={answerRef}>
                {answerLoading || answer ? (
                  <AnswerPanel
                    answer={answer}
                    loading={answerLoading}
                    connectedRepo={connectedRepo}
                    history={history}
                  />
                ) : (
                  <div className="empty-state fade-in delay-1">
                    <div className="empty-state-icon">
                      <Bot size={22} />
                    </div>
                    <div className="empty-state-title">GitHub MCP Workspace</div>
                    <div className="empty-state-text">
                      Select or enter a repository on the left, then ask any question about the codebase.
                      GiTMCP connects directly to GitHub via MCP without cloning or embedding.
                    </div>
                  </div>
                )}
              </div>

              {/* Query form */}
              <div style={{
                borderTop: (answerLoading || answer) ? "1px solid var(--border-subtle)" : "none",
                paddingTop: (answerLoading || answer) ? 24 : 0,
              }}>
                <QueryPanel
                  connectedRepo={connectedRepo}
                  onAnswer={setAnswer}
                  onLoading={setAnswerLoading}
                  isLoading={answerLoading}
                  history={history}
                  onHistoryChange={setHistory}
                />
              </div>
            </div>
          </div>
        </div>
      )}
    </>
  );
}
