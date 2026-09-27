import assert from "node:assert/strict";
import { test } from "node:test";
import { applySetCookies, parseSetCookie } from "../src/lib/cookies.ts";

// Stands in for Astro.cookies, recording what a page would hand the browser.
const recorder = () => {
  const calls = [];
  return { calls, set: (name, value, options) => calls.push({ name, value, options }) };
};

test("carries every attribute the backend sets on the session cookie", () => {
  const parsed = parseSetCookie(
    "candlr_token=abc.def; HttpOnly; Max-Age=604800; Path=/; SameSite=lax; Secure"
  );
  assert.equal(parsed.name, "candlr_token");
  assert.equal(parsed.value, "abc.def");
  assert.deepEqual(parsed.options, {
    httpOnly: true,
    maxAge: 604800,
    path: "/",
    sameSite: "lax",
    secure: true,
  });
});

// FastAPI's delete_cookie sends a DQUOTE-wrapped empty value, which RFC 6265
// allows. Max-Age=0 is what clears it, so the value is passed through as-is
// rather than unwrapped.
test("keeps the value verbatim when the backend clears a cookie", () => {
  const parsed = parseSetCookie('candlr_2fa=""; Max-Age=0; Path=/');
  assert.equal(parsed.name, "candlr_2fa");
  assert.equal(parsed.value, '""');
  assert.equal(parsed.options.maxAge, 0);
});

test("ignores attributes Astro has no option for, and unparseable headers", () => {
  assert.deepEqual(parseSetCookie("a=b; Priority=High; Partitioned").options, {});
  assert.equal(parseSetCookie("not-a-cookie"), null);
  assert.equal(parseSetCookie("=novalue"), null);
});

test("applies each Set-Cookie separately rather than the joined header", () => {
  const headers = new Headers();
  headers.append("set-cookie", 'candlr_2fa=""; Max-Age=0; Path=/');
  headers.append("set-cookie", "candlr_token=abc.def; Path=/; HttpOnly");
  const cookies = recorder();

  applySetCookies(cookies, headers);

  assert.deepEqual(
    cookies.calls.map(({ name, value }) => [name, value]),
    [
      ["candlr_2fa", '""'],
      ["candlr_token", "abc.def"],
    ]
  );
});
