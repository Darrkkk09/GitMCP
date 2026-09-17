import { useEffect, useState } from "react";
import { api } from "../api";

export default function GitHubConnection({ account, onAccountChange, onSelect, disabled }) {
  const [repositories, setRepositories] = useState([]);
  const [nextPage, setNextPage] = useState(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState("");

  async function load(page = 1) {
    setLoading(true);
    setError("");
    try {
      const data = await api.githubRepos(page);
      setRepositories(previous => page === 1 ? data.repositories : [...previous, ...data.repositories]);
      setNextPage(data.next_page);
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }

  useEffect(() => {
    if (account?.github_connected) load();
    else { setRepositories([]); setNextPage(null); }
  }, [account?.user?.id, account?.github_connected]);

  async function disconnect() {
    setLoading(true);
    try {
      const result = await api.disconnect();
      onSelect("");
      await onAccountChange();
      if (!result.revoked_on_github) setError("Disconnected locally. Remove GiTMCP from GitHub Settings → Applications to finish revoking access.");
    } catch (e) { setError(e.message); }
    finally { setLoading(false); }
  }

  return <section className="sidebar-section">
    <div className="section-label">GitHub Connection</div>
    {account?.github_connected ? <>
      <p>GitHub Connected ✓ <strong>{account.user.github_login}</strong></p>
      <label className="field-label" htmlFor="github-repository">Your accessible repositories</label>
      <select id="github-repository" className="input-field" defaultValue="" disabled={disabled || loading}
        onChange={e => onSelect(e.target.value)}>
        <option value="">Select a repository</option>
        {repositories.map(repo => <option key={repo.id} value={repo.full_name}>
          {repo.full_name}{repo.private ? " (private)" : ""}
        </option>)}
      </select>
      {nextPage && <button className="btn btn-secondary btn-sm" disabled={loading} onClick={() => load(nextPage)}>Load more</button>}
      <button className="btn btn-secondary btn-sm" disabled={loading || disabled} onClick={disconnect}>Disconnect GitHub</button>
    </> : <a className="btn btn-primary" href={api.loginUrl}>Connect GitHub</a>}
    {loading && <p>Loading GitHub connection…</p>}
    {error && <p role="alert" className="status-msg error">{error}</p>}
  </section>;
}
