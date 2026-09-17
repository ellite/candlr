// Server-side API client - called from Astro pages and API routes, never from the browser.
// process.env is used (not import.meta.env) because Vite replaces import.meta.env at build
// time and won't see Docker runtime environment variables.
const BASE = process.env.API_URL ?? "http://backend:8000";

export async function apiFetch(
  path: string,
  options: RequestInit & { cookies?: string } = {}
): Promise<Response> {
  const { cookies, ...init } = options;
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string>),
  };
  if (cookies) headers["Cookie"] = cookies;

  return fetch(`${BASE}${path}`, { ...init, headers });
}

export async function apiJson<T>(
  path: string,
  options: RequestInit & { cookies?: string } = {}
): Promise<T> {
  const res = await apiFetch(path, options);
  if (!res.ok) {
    const detail = await res.json().catch(() => ({ detail: res.statusText }));
    throw { status: res.status, detail: detail.detail ?? res.statusText };
  }
  return res.json() as Promise<T>;
}
