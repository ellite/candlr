import type { APIRoute } from "astro";
import { apiFetch } from "../../lib/api";

// Catch-all proxy: forwards every /api/* request to the FastAPI backend,
// stripping the /api prefix so /api/auth/login → /auth/login on the backend.
export const ALL: APIRoute = async ({ request, params }) => {
  const path = params.path ?? "";
  const url = new URL(request.url);
  const backendPath = `/${path}${url.search}`;
  const cookieHeader = request.headers.get("cookie") ?? "";

  // Read as raw bytes, not text: text() decodes as UTF-8, which is lossy
  // for binary bodies (file uploads, image responses) - it silently
  // corrupts them rather than erroring, so this has to be arrayBuffer()
  // even though most requests here are plain JSON.
  const body = ["GET", "HEAD"].includes(request.method) ? undefined : await request.arrayBuffer();

  let upstreamRes: Response;
  try {
    upstreamRes = await apiFetch(backendPath, {
      method: request.method,
      cookies: cookieHeader,
      body: body && body.byteLength > 0 ? body : undefined,
      headers: {
        "Content-Type": request.headers.get("content-type") ?? "application/json",
      },
    });
  } catch (e) {
    return new Response(JSON.stringify({ detail: "Could not reach backend" }), {
      status: 502,
      headers: { "content-type": "application/json" },
    });
  }

  const headers = new Headers();
  const contentType = upstreamRes.headers.get("content-type");
  if (contentType) headers.set("content-type", contentType);
  const cacheControl = upstreamRes.headers.get("cache-control");
  if (cacheControl) headers.set("cache-control", cacheControl);
  const setCookie = upstreamRes.headers.get("set-cookie");
  if (setCookie) headers.set("set-cookie", setCookie);

  const responseBody = upstreamRes.status === 204 ? null : await upstreamRes.arrayBuffer();

  return new Response(responseBody, { status: upstreamRes.status, headers });
};
