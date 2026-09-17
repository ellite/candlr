// Candlr service worker: handles incoming Web Push notifications, and
// caches the app shell (dashboard, events, calendar, settings, plus the static
// assets they load) so those pages still open from cache when offline.
//
// Caching strategy is network-first: every request always tries the network
// first and updates the cache with whatever comes back, only falling back
// to the cached copy when the network request itself fails. This is
// deliberately not cache-first/stale-while-revalidate - a self-hosted app
// like this one should always show live data when there's a connection,
// and the cache exists purely as a fallback for being offline, not as a
// speed optimization.

const CACHE_VERSION = "v6";
const CACHE_NAME = `candlr-shell-${CACHE_VERSION}`;

// Only full page loads for these routes are cached. Static assets are
// matched by path prefix below instead of listed here, since their
// filenames are content-hashed per build and unknown ahead of time.
//
// "/login" is included even though register/forgot-password/etc. still
// aren't, and still need a live connection to do anything useful: every
// protected route's middleware redirect lands on "/login" (never on those
// other public pages), and a redirected navigation is a *second*, separate
// "fetch" event for "/login" - the request object for the first ("/",
// mode: "navigate") has redirect: "manual", so the SW's own fetch(request)
// resolves to an opaque, unreadable "opaqueredirect" response instead of
// transparently following it the way a plain fetch() would, and the
// browser turns that into a brand new top-level navigation to the
// redirect's target. If that target isn't cacheable, offline users whose
// session cookie has expired (a week-old dashboard tab reopened without
// signal, say) hit the browser's own hard offline error page instead of
// anything Candlr renders. See networkFirst() below for where this shows up.
const CACHED_PAGES = ["/", "/events", "/calendar", "/settings", "/login"];

function isCacheable(url) {
  if (url.origin !== self.location.origin) return false;
  if (CACHED_PAGES.includes(url.pathname)) return true;
  if (url.pathname.startsWith("/_astro/")) return true;
  if (url.pathname.startsWith("/icons/")) return true;
  if (url.pathname.startsWith("/branding/")) return true;
  return (
    url.pathname === "/favicon.svg" ||
    url.pathname === "/favicon.ico" ||
    url.pathname === "/favicon-v4.png" ||
    url.pathname === "/apple-touch-icon.png" ||
    url.pathname === "/manifest.webmanifest"
  );
}

async function networkFirst(request) {
  const cache = await caches.open(CACHE_NAME);
  try {
    const response = await fetch(request);
    if (response.ok) cache.put(request, response.clone());
    return response;
  } catch (err) {
    const cached = await cache.match(request);
    if (cached) return cached;
    throw err;
  }
}

// Precaching just the page URLs isn't enough on its own: the fetch
// handler below only ever sees requests for a page's own <script>/<link>
// assets once that page has been loaded *while the worker is already
// controlling the tab*, which is never true for the very first page that
// triggers registration. So on install, also pull each hashed /_astro/*
// asset straight out of the fetched HTML and cache those too - otherwise a
// page could be "cached" as an inert, unstyled shell missing the JS it
// needs to actually do anything.
async function precacheShell(cache) {
  await Promise.all(
    CACHED_PAGES.map(async (path) => {
      try {
        const response = await fetch(path, { credentials: "same-origin" });
        if (!response.ok) return;
        const html = await response.clone().text();
        await cache.put(path, response);
        const assetUrls = [...html.matchAll(/\/_astro\/[^"'\s]+/g)].map((m) => m[0]);
        await Promise.all(
          assetUrls.map((assetUrl) =>
            fetch(assetUrl)
              .then((res) => (res.ok ? cache.put(assetUrl, res) : null))
              .catch(() => null)
          )
        );
      } catch {
        // Offline or not logged in yet at install time - the network-first
        // fetch handler below fills the cache in as pages are actually
        // visited instead.
      }
    })
  );
}

self.addEventListener("install", (event) => {
  event.waitUntil(caches.open(CACHE_NAME).then(precacheShell));
  self.skipWaiting();
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    Promise.all([
      self.clients.claim(),
      caches
        .keys()
        .then((keys) => Promise.all(keys.filter((key) => key.startsWith("candlr-shell-") && key !== CACHE_NAME).map((key) => caches.delete(key)))),
    ])
  );
});

self.addEventListener("fetch", (event) => {
  if (event.request.method !== "GET") return;
  const url = new URL(event.request.url);
  if (!isCacheable(url)) return; // API calls and everything else go straight to the network, untouched
  event.respondWith(networkFirst(event.request));
});

self.addEventListener("push", (event) => {
  let data = { title: "Candlr", body: "" };
  try {
    if (event.data) data = { ...data, ...event.data.json() };
  } catch {
    if (event.data) data.body = event.data.text();
  }

  event.waitUntil(
    self.registration.showNotification(data.title || "Candlr", {
      body: data.body || "",
      data: { url: data.url || "/" },
      icon: "/icons/icon-192.png?v=4",
      badge: "/favicon-v4.png",
    })
  );
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  let target = new URL("/", self.location.origin);
  try {
    const requested = new URL(event.notification.data?.url || "/", self.location.origin);
    if (requested.origin === self.location.origin) target = requested;
  } catch {
    // Old notifications and malformed destinations fall back to the dashboard.
  }
  event.waitUntil((async () => {
    const clients = await self.clients.matchAll({ type: "window", includeUncontrolled: true });
    for (const client of clients) {
      if (new URL(client.url).origin !== self.location.origin) continue;
      try {
        const navigated = await client.navigate(target.href);
        if (navigated) return await navigated.focus();
      } catch {
        // A closing window should not prevent opening the requested card.
      }
    }
    return self.clients.openWindow(target.href);
  })());
});
