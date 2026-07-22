// 后端 REST 客户端。API_BASE 默认同源（本地 Web 模式由 FastAPI 托管）；
// Tauri 侧车模式可在构建时注入 VITE_API_BASE。
const env = (import.meta as any).env || {};
const API_BASE: string = env.VITE_API_BASE || "";

async function req(method: string, path: string, body?: any, isForm = false): Promise<any> {
  const opts: RequestInit = { method };
  if (body !== undefined) {
    if (isForm) {
      opts.body = body;
    } else {
      opts.headers = { "Content-Type": "application/json" };
      opts.body = JSON.stringify(body);
    }
  }
  const r = await fetch(API_BASE + path, opts);
  if (!r.ok) {
    const txt = await r.text();
    throw new Error(`${path} -> ${r.status} ${txt}`);
  }
  const ct = r.headers.get("content-type") || "";
  return ct.includes("application/json") ? r.json() : r.text();
}

export const api = {
  listRepos: () => req("GET", "/repos"),
  createRepo: (name: string, ns: string, path?: string) =>
    req("POST", "/repos", { name, namespace: ns, path: path || null }),
  switchRepo: (name: string) => {
    const fd = new FormData();
    fd.append("name", name);
    return req("POST", "/repos/switch", fd, true);
  },
  getTree: (name: string, path = "") =>
    req("GET", `/tree?name=${encodeURIComponent(name)}&path=${encodeURIComponent(path)}`),
  mkdir: (name: string, parentPath: string, folderName: string) => {
    const fd = new FormData();
    fd.append("name", name);
    fd.append("parent_path", parentPath);
    fd.append("folder_name", folderName);
    return req("POST", "/tree/mkdir", fd, true);
  },
  rmdir: (name: string, folderPath: string) => {
    const fd = new FormData();
    fd.append("name", name);
    fd.append("folder_path", folderPath);
    return req("DELETE", "/tree/rmdir", fd, true);
  },
  upload: (name: string, folder: string, file: File) => {
    const fd = new FormData();
    fd.append("name", name);
    fd.append("folder", folder);
    fd.append("file", file);
    return req("POST", "/upload", fd, true);
  },
  processed: (name: string) =>
    req("GET", `/processed?name=${encodeURIComponent(name)}`),
  clearMarkers: (name: string) => {
    const fd = new FormData();
    fd.append("name", name);
    return req("POST", "/markers/clear", fd, true);
  },
  process: (name: string, selection: any, force = false, outDir = "") => {
    const fd = new FormData();
    fd.append("name", name);
    if (selection) fd.append("selection", JSON.stringify(selection));
    fd.append("force", force ? "true" : "false");
    fd.append("out_dir", outDir);
    return req("POST", "/process", fd, true);
  },
  processStatus: () => req("GET", "/process/status"),
  bundle: (name: string) =>
    req("GET", `/bundle?name=${encodeURIComponent(name)}`),
  getSettings: () => req("GET", "/settings"),
  updateSettings: (data: any) => req("POST", "/settings", data),
  getReposBase: () => req("GET", "/repos/base"),
};
