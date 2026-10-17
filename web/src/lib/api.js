export const API = (import.meta.env.VITE_API_BASE_URL || "http://localhost:8000").replace(/\/$/, "");

const json = async (r) => {
  if (!r.ok) throw new Error(`${r.status} ${r.statusText}`);
  return r.json();
};
const enc = encodeURIComponent;

export const getTools = () => fetch(`${API}/api/tools`).then(json);
export const getToolCode = (n) => fetch(`${API}/api/tools/${enc(n)}/code`).then(json);
export const deleteTool = (n) => fetch(`${API}/api/tools/${enc(n)}`, { method: "DELETE" }).then(json);
export const clearMemory = () => fetch(`${API}/api/chat/clear`, { method: "POST" }).then(json);
export const streamUrl = (prompt) => `${API}/api/chat/stream?prompt=${enc(prompt)}`;