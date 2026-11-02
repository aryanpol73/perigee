// Perigee's service worker: the installed app opens fast and works offline with the
// last data it saw, and it can tell the user when new data is published.
//   pages and /data/*   network first, the saved copy when offline (data changes daily)
//   /assets, /cesium,   saved on first use (hashed or versioned files never change)
//   /pyodide, /icons
//   NASA imagery tiles  saved on first use, at most MAX_TILES of them
// Notifications: the page keeps its settings in the "perigee-state" cache; where the
// browser allows periodic background sync (installed apps on Android), a daily check
// for new data runs here even when the app is closed.
const VERSION = "v1";
const SHELL = `perigee-shell-${VERSION}`;
const STATIC = `perigee-static-${VERSION}`;
const DATA = `perigee-data-${VERSION}`;
const TILES = `perigee-tiles-${VERSION}`;
const STATE = "perigee-state";
const KEEP = [SHELL, STATIC, DATA, TILES, STATE];
const MAX_TILES = 800;
const GIBS = "https://gibs.earthdata.nasa.gov";

self.addEventListener("install", (event) => {
  event.waitUntil(
    caches
      .open(SHELL)
      .then((c) => c.addAll(["/", "/manifest.webmanifest", "/icons/icon-192.png", "/icons/badge-96.png"]))
      .then(() => self.skipWaiting()),
  );
});

self.addEventListener("activate", (event) => {
  event.waitUntil(
    caches
      .keys()
      .then((names) => Promise.all(names.filter((n) => n.startsWith("perigee-") && !KEEP.includes(n)).map((n) => caches.delete(n))))
      .then(() => self.clients.claim()),
  );
});

async function networkFirst(request, cacheName, key = request) {
  const cache = await caches.open(cacheName);
  try {
    const response = await fetch(request);
    if (response.ok) await cache.put(key, response.clone());
    return response;
  } catch (error) {
    const saved = await cache.match(key);
    if (saved) return saved;
    throw error;
  }
}

async function cacheFirst(request, cacheName, limit = 0) {
  const cache = await caches.open(cacheName);
  const saved = await cache.match(request);
  if (saved) return saved;
  const response = await fetch(request);
  if (response.ok) {
    await cache.put(request, response.clone());
    if (limit) {
      const keys = await cache.keys();
      // Oldest first: drop the earliest saved tiles beyond the limit.
      await Promise.all(keys.slice(0, Math.max(0, keys.length - limit)).map((k) => cache.delete(k)));
    }
  }
  return response;
}

self.addEventListener("fetch", (event) => {
  const request = event.request;
  if (request.method !== "GET") return;
  const url = new URL(request.url);
  if (url.origin === GIBS) {
    event.respondWith(cacheFirst(request, TILES, MAX_TILES));
    return;
  }
  if (url.origin !== self.location.origin) return;
  if (request.mode === "navigate") {
    // Every page is the one app page; ?now= and the hash do not change it.
    event.respondWith(networkFirst(request, SHELL, "/"));
  } else if (url.pathname.startsWith("/data/")) {
    event.respondWith(networkFirst(request, DATA));
  } else if (/^\/(assets|cesium|pyodide|icons)\//.test(url.pathname)) {
    event.respondWith(cacheFirst(request, STATIC));
  }
});

// --- Notifications ---------------------------------------------------------------

async function readState() {
  const saved = await (await caches.open(STATE)).match("/__state");
  return saved ? saved.json() : {};
}

async function writeState(state) {
  await (await caches.open(STATE)).put("/__state", new Response(JSON.stringify(state), { headers: { "Content-Type": "application/json" } }));
}

async function checkForNewData() {
  const state = await readState();
  if (!state.enabled || !state.newData) return;
  const meta = await (await fetch("/data/meta.json", { cache: "no-store" })).json();
  if (!meta.snapshot_id || meta.snapshot_id === state.lastRun) return;
  const first = !state.lastRun;
  await writeState({ ...state, lastRun: meta.snapshot_id });
  if (first) return;
  const watched = (state.watched && state.watchCount) || 0;
  await self.registration.showNotification("New orbit data in Perigee", {
    body:
      `${Number(meta.conjunction_count).toLocaleString("en-US")} close approaches estimated for the next 7 days.` +
      (watched ? ` Open to check your ${watched} watched object${watched === 1 ? "" : "s"}.` : ""),
    icon: "/icons/icon-192.png",
    badge: "/icons/badge-96.png",
    tag: "perigee-new-data",
    data: { url: "/?app=1#/conjunctions" },
  });
}

self.addEventListener("periodicsync", (event) => {
  if (event.tag === "perigee-new-data") event.waitUntil(checkForNewData().catch(() => undefined));
});

self.addEventListener("notificationclick", (event) => {
  event.notification.close();
  const target = new URL(event.notification.data?.url ?? "/?app=1", self.location.origin).href;
  event.waitUntil(
    self.clients.matchAll({ type: "window", includeUncontrolled: true }).then((windows) => {
      const open = windows.find((w) => new URL(w.url).origin === self.location.origin);
      if (open) return open.focus().then((w) => w?.navigate?.(target));
      return self.clients.openWindow(target);
    }),
  );
});
