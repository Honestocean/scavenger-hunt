/* Service worker for the scavenger hunt: keeps the app usable with no signal.
 *
 * - Precaches the app shell (the page and the vendored Leaflet).
 * - Same-origin GET only, network-first: a phone with signal always gets the
 *   newest files; if the network fails or takes longer than NETWORK_TIMEOUT_MS
 *   the cached copy is used instead. Every good network answer refreshes the
 *   cache. Cross-origin requests (map tiles) are never touched.
 *
 * All URLs are relative, so this works under any sub-path (GitHub Pages).
 */
"use strict";

const CACHE_PREFIX = "scavenger-hunt-shell-";
const CACHE = CACHE_PREFIX + "v1";
const NETWORK_TIMEOUT_MS = 3000;
const SHELL = [
  "./",
  "index.html",
  "vendor/leaflet/leaflet.js",
  "vendor/leaflet/leaflet.css",
  "vendor/leaflet/images/marker-icon.png",
  "vendor/leaflet/images/marker-icon-2x.png",
  "vendor/leaflet/images/marker-shadow.png",
  "vendor/leaflet/images/layers.png",
  "vendor/leaflet/images/layers-2x.png"
];

// Cache key: the URL without its query string or hash.
function keyOf(url) {
  const u = new URL(url, self.location.href);
  u.search = "";
  u.hash = "";
  return u.href;
}

// "no-cache" = always ask the server (a cheap 304 if unchanged), so the
// browser's own HTTP cache can never hand back a stale index.html.
function fromNetwork(url) {
  return fetch(url, { cache: "no-cache", credentials: "same-origin", redirect: "follow" });
}

async function store(url, response) {
  if (!response || !response.ok || response.status !== 200 || response.type !== "basic") return;
  let copy = response.clone();
  if (copy.redirected) {
    // A redirected response may not answer a page navigation; store a clean copy.
    copy = new Response(await copy.blob(), { status: 200, statusText: copy.statusText, headers: copy.headers });
  }
  const cache = await caches.open(CACHE);
  const key = keyOf(url);
  // "./" and "index.html" are the same page: keep both entries equally new.
  const root = keyOf("./"), index = keyOf("index.html");
  if (key === root || key === index) {
    await cache.put(key === root ? index : root, copy.clone());
  }
  await cache.put(key, copy);
}

async function fromCache(url) {
  const cache = await caches.open(CACHE);
  const key = keyOf(url);
  let hit = await cache.match(key, { ignoreSearch: true });
  if (hit) return hit;
  // The page is cached as both "./" and "index.html"; either stands in for the other.
  const root = keyOf("./"), index = keyOf("index.html");
  if (key === root) hit = await cache.match(index);
  else if (key === index) hit = await cache.match(root);
  return hit || null;
}

// Fetch shell files into the cache. Each entry has its own try, so one failure
// never stops the others (or the install). onlyMissing skips what is already there.
async function precache(onlyMissing) {
  await Promise.all(SHELL.map(async url => {
    try {
      if (onlyMissing) {
        const cache = await caches.open(CACHE);
        if (await cache.match(keyOf(url))) return;
      }
      await store(url, await fromNetwork(url));
    } catch (e) { /* tried again on the next page load */ }
  }));
}

self.addEventListener("install", event => {
  event.waitUntil((async () => {
    await precache(false);
    await precache(true); // second chance for anything that failed
    await self.skipWaiting();
  })());
});

self.addEventListener("activate", event => {
  event.waitUntil((async () => {
    try {
      const names = await caches.keys();
      // Only this app's old caches: other sites on the same origin keep theirs.
      await Promise.all(names.filter(n => n.startsWith(CACHE_PREFIX) && n !== CACHE).map(n => caches.delete(n)));
    } catch (e) { /* ignore */ }
    await self.clients.claim();
  })());
});

self.addEventListener("fetch", event => {
  const request = event.request;
  if (request.method !== "GET") return;
  let url;
  try { url = new URL(request.url); } catch (e) { return; }
  if (url.origin !== self.location.origin) return;   // map tiles etc.: not ours
  if (request.headers.has("range")) return;

  // Keeps running after a timeout so a slow answer still refreshes the cache.
  const network = (async () => {
    const response = await fromNetwork(request.url);
    try { await store(request.url, response); } catch (e) { /* cache full or unavailable */ }
    return response;
  })();
  event.waitUntil(network.catch(() => {}));
  // Every page load also fills in any shell file an earlier, patchy visit missed.
  if (request.mode === "navigate") event.waitUntil(network.then(() => precache(true)).catch(() => {}));

  event.respondWith((async () => {
    let timer;
    const timeout = new Promise(resolve => { timer = setTimeout(() => resolve(null), NETWORK_TIMEOUT_MS); });
    try {
      const response = await Promise.race([network, timeout]);
      clearTimeout(timer);
      if (response) {
        if (response.ok || response.status === 304) return response;
        // A server error page: prefer a good cached copy if there is one.
        return (await fromCache(request.url).catch(() => null)) || response;
      }
    } catch (e) {
      clearTimeout(timer); // no network: fall through to the cache
    }
    const cached = await fromCache(request.url).catch(() => null);
    if (cached) return cached;
    return network; // nothing cached: wait for the network, however slow (rejects if offline)
  })());
});
