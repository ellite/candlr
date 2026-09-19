import { defineMiddleware } from "astro:middleware";

const PUBLIC_PAGES = [
  "/login",
  "/two-factor",
  "/register",
  "/oidc-start",
  "/oidc-callback",
  "/forgot-password",
  "/reset-password",
];

const PUBLIC_API_ROUTES = [
  "/api/health",
  "/api/auth/login",
  "/api/auth/2fa/verify",
  "/api/auth/register",
  "/api/auth/logout",
  "/api/auth/registration-status",
  "/api/auth/password-reset-status",
  "/api/auth/forgot-password",
  "/api/auth/reset-password",
  "/api/oidc/config",
  "/api/oidc/authorize",
  "/api/oidc/exchange",
  // Trailing slash matters: only the token-in-URL .ics route is public, not
  // the session-authenticated /api/calendar/feed management endpoints.
  "/api/calendar/feed/",
];

const SECURITY_HEADERS: Record<string, string> = {
  "X-Content-Type-Options": "nosniff",
  "X-Frame-Options": "DENY",
  "Referrer-Policy": "strict-origin-when-cross-origin",
  "Content-Security-Policy":
    "default-src 'self'; " +
    "script-src 'self' 'unsafe-inline'; " +
    "style-src 'self' 'unsafe-inline'; " +
    "img-src 'self' data: blob:; " +
    "connect-src 'self'; " +
    "font-src 'self'; " +
    "frame-ancestors 'none';",
};

export const onRequest = defineMiddleware(async (context, next) => {
  const { pathname } = context.url;

  if (
    PUBLIC_PAGES.some((r) => pathname.startsWith(r)) ||
    PUBLIC_API_ROUTES.some((r) => pathname.startsWith(r)) ||
    pathname.startsWith("/_astro")
  ) {
    const response = await next();
    for (const [key, value] of Object.entries(SECURITY_HEADERS)) {
      response.headers.set(key, value);
    }
    return response;
  }

  const token = context.cookies.get("candlr_token");
  if (!token?.value) {
    return context.redirect("/login");
  }

  const response = await next();
  for (const [key, value] of Object.entries(SECURITY_HEADERS)) {
    response.headers.set(key, value);
  }
  return response;
});
