// Thin fetch wrapper. Reads JWT from localStorage and attaches it.

export type Job = {
  id: string;
  source_filename: string;
  source_kind: "image" | "pdf" | "dir";
  mode: "full" | "text-only";
  status: "queued" | "running" | "done" | "failed" | "canceled";
  page_count: number;
  current_page: number;
  progress_pct: number;
  duration_seconds: number;
  error_msg: string | null;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  queue_position: number;
  eta_seconds: number;
};

export type VersionInfo = {
  commit: string;
  short_commit: string;
  behind: number;
  ahead: number;
  branch: string;
  remote_url: string;
  auto_update: boolean;
  updating: boolean;
  last_check: string | null;
  sandbox_backend: string;
  sandbox_allow_network: boolean;
};

export type Me = { id: number; username: string; is_admin: boolean };

const TOKEN_KEY = "deckweaver.token";

export const auth = {
  get token() {
    return localStorage.getItem(TOKEN_KEY);
  },
  setToken(t: string) {
    localStorage.setItem(TOKEN_KEY, t);
  },
  clear() {
    localStorage.removeItem(TOKEN_KEY);
  },
};

async function req<T>(path: string, init: RequestInit = {}): Promise<T> {
  const headers = new Headers(init.headers || {});
  if (auth.token) headers.set("Authorization", `Bearer ${auth.token}`);
  if (!(init.body instanceof FormData) && init.body && !headers.has("Content-Type")) {
    headers.set("Content-Type", "application/json");
  }
  const res = await fetch(path, { ...init, headers });
  if (res.status === 401) {
    auth.clear();
    if (location.pathname !== "/login") location.replace("/login");
    throw new Error("Unauthorized");
  }
  if (!res.ok) {
    let msg = res.statusText;
    try {
      const body = await res.json();
      msg = body.detail || msg;
    } catch {}
    throw new Error(msg);
  }
  if (res.status === 204) return undefined as unknown as T;
  return res.json();
}

export type PowChallenge = {
  challenge: string;
  difficulty: number;
  issued_at: number;
};

export type BulkDeleteResult = {
  deleted: string[];
  skipped: { id: string; reason: string }[];
  storage_freed_mb: number;
};

export const api = {
  powChallenge: () => req<PowChallenge>("/api/auth/pow"),
  login: (
    username: string,
    password: string,
    pow_challenge: string,
    pow_nonce: string,
  ) =>
    req<{ access_token: string; username: string; is_admin: boolean }>("/api/auth/login", {
      method: "POST",
      body: JSON.stringify({ username, password, pow_challenge, pow_nonce }),
    }),
  me: () => req<Me>("/api/auth/me"),
  listJobs: () => req<Job[]>("/api/jobs"),
  getJob: (id: string) => req<Job>(`/api/jobs/${id}`),
  createJob: (mode: "full" | "text-only", files: File[], name?: string) => {
    const fd = new FormData();
    fd.append("mode", mode);
    if (name && name.trim()) fd.append("name", name.trim());
    for (const f of files) fd.append("files", f);
    return req<Job>("/api/jobs", { method: "POST", body: fd });
  },
  deleteJob: (id: string, force = false) =>
    req<void>(`/api/jobs/${id}${force ? "?force=true" : ""}`, { method: "DELETE" }),
  bulkDeleteJobs: (ids: string[], force = false) =>
    req<BulkDeleteResult>("/api/jobs/bulk-delete", {
      method: "POST",
      body: JSON.stringify({ ids, force }),
    }),
  cancelJob: (id: string) => req<Job>(`/api/jobs/${id}/cancel`, { method: "POST" }),
  retryJob: (id: string) => req<Job>(`/api/jobs/${id}/retry`, { method: "POST" }),
  jobLogs: (id: string) => req<{ id: string; log_tail: string }>(`/api/jobs/${id}/logs`),
  downloadUrl: (id: string) => `/api/jobs/${id}/download?token=${encodeURIComponent(auth.token || "")}`,
  version: () => req<VersionInfo>("/api/system/version"),
  triggerUpdate: () => req<{ started: boolean }>("/api/system/update", { method: "POST" }),
  setAutoUpdate: (enabled: boolean) =>
    req<{ auto_update: boolean }>("/api/system/auto-update", {
      method: "POST",
      body: JSON.stringify({ enabled }),
    }),
  sweepOrphans: () =>
    req<{ swept: number }>("/api/system/sweep-orphans", { method: "POST" }),
};
