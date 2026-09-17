// Deterministic color palette for repos
const REPO_COLORS = [
  "#6366f1", "#8b5cf6", "#ec4899", "#14b8a6",
  "#f59e0b", "#10b981", "#3b82f6", "#f97316",
];

const colorMap = new Map();
let colorIdx = 0;

export function getRepoColor(repoName) {
  if (!colorMap.has(repoName)) {
    colorMap.set(repoName, REPO_COLORS[colorIdx % REPO_COLORS.length]);
    colorIdx++;
  }
  return colorMap.get(repoName);
}

export function timeAgo(ms) {
  const s = Math.floor(ms / 1000);
  if (s < 60) return `${s}s`;
  return `${Math.floor(s / 60)}m ${s % 60}s`;
}
