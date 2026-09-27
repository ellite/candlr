// Re-issues the backend's Set-Cookie headers through Astro.cookies.
//
// Astro.redirect() builds a fresh Response carrying only Location, so headers
// appended to Astro.response never reach the browser. Cookies are the one
// thing Astro re-attaches to whatever a page returns, which makes this the
// only way to hand a backend session cookie to the browser and redirect in
// the same render.
import type { AstroCookies, AstroCookieSetOptions } from "astro";

export type ParsedSetCookie = {
  name: string;
  value: string;
  options: AstroCookieSetOptions;
};

export function parseSetCookie(header: string): ParsedSetCookie | null {
  const [pair, ...attributes] = header.split(";");
  const separator = pair.indexOf("=");
  if (separator < 1) return null;

  const name = pair.slice(0, separator).trim();
  const value = pair.slice(separator + 1).trim();
  const options: AstroCookieSetOptions = {};

  for (const attribute of attributes) {
    const index = attribute.indexOf("=");
    const key = (index === -1 ? attribute : attribute.slice(0, index)).trim().toLowerCase();
    const raw = index === -1 ? "" : attribute.slice(index + 1).trim();

    switch (key) {
      case "path":
        options.path = raw;
        break;
      case "domain":
        options.domain = raw;
        break;
      case "max-age": {
        const maxAge = Number(raw);
        if (Number.isFinite(maxAge)) options.maxAge = maxAge;
        break;
      }
      case "expires": {
        const expires = new Date(raw);
        if (!Number.isNaN(expires.getTime())) options.expires = expires;
        break;
      }
      case "httponly":
        options.httpOnly = true;
        break;
      case "secure":
        options.secure = true;
        break;
      case "samesite": {
        const sameSite = raw.toLowerCase();
        if (sameSite === "lax" || sameSite === "strict" || sameSite === "none") {
          options.sameSite = sameSite;
        }
        break;
      }
    }
  }

  return { name, value, options };
}

export function applySetCookies(cookies: AstroCookies, headers: Headers): void {
  // getSetCookie() keeps the headers separate; headers.get() would join them
  // into one comma-separated string, which is not a valid Set-Cookie value.
  for (const header of headers.getSetCookie()) {
    const parsed = parseSetCookie(header);
    if (parsed) cookies.set(parsed.name, parsed.value, parsed.options);
  }
}
