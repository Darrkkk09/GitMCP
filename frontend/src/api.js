export const API_BASE = import.meta.env.VITE_API_BASE_URL || "http://localhost:8000";
let csrfToken = "";

async function request(path, options = {}) {
  const res = await fetch(`${API_BASE}${path}`, {
    ...options,
    credentials: "include",
    headers: { "Content-Type": "application/json", "X-CSRF-Token": csrfToken, ...options.headers },
  });
  if (!res.ok) {
    const error = await res.json().catch(() => ({}));
    const err = new Error(typeof error.detail === "string" ? error.detail : "Request failed");
    err.status = res.status;
    throw err;
  }
  return res.json();
}


export const api = {
  loginUrl: `${API_BASE}/auth/github`,
  async me() {
    const data = await request("/auth/me");
    csrfToken = data.csrf_token;
    return data;
  },
  githubRepos: (page = 1) => request(`/github/repos?page=${page}`),
  disconnect: () => request("/auth/github", { method: "DELETE" }),
  async logout() {
    const data = await request("/auth/logout", { method: "POST" });
    csrfToken = "";
    return data;
  },
  queryMCP: (githubRepo, question, history = []) => request("/query/mcp", {
    method: "POST", body: JSON.stringify({ github_repo: githubRepo, question, history, mode: "mcp" }),
  }),
};
