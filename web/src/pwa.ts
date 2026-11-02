// The installable app: which layout to show, the service worker, and whether newer
// data has been published since the page loaded.
//
// The mobile layout is for the installed app only (home-screen PWA or the Android
// APK); the website keeps the desktop dashboard. ?app=1 shows the app layout in any
// browser (preview, and the installed app's start URL), ?app=0 the dashboard.
import { apiUrl } from "./api";

/** True when this page should show the mobile app layout. Decided once, at load. */
export function isAppMode(win: Window = window): boolean {
  const flag = new URLSearchParams(win.location.search).get("app");
  if (flag === "0") return false;
  if (flag === "1") return true;
  const installed =
    win.matchMedia?.("(display-mode: standalone)").matches ||
    win.matchMedia?.("(display-mode: fullscreen)").matches ||
    (win.navigator as Navigator & { standalone?: boolean }).standalone === true || // iOS home screen
    win.document.referrer.startsWith("android-app://"); // the Android app (a Trusted Web Activity)
  // An app installed on a desktop opens a wide window: the dashboard suits it better.
  const handheld = win.matchMedia?.("(pointer: coarse)").matches || win.matchMedia?.("(max-width: 900px)").matches;
  return Boolean(installed && handheld);
}

/** Production builds only: the dev server's files change on every edit. */
export function registerServiceWorker(): void {
  if (!import.meta.env.PROD || !("serviceWorker" in navigator)) return;
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => undefined); // the site works without it
  });
}

const DATA = (import.meta.env.VITE_DATA_BASE ?? "").replace(/\/$/, "");

/** The snapshot the server publishes now (not the one this page loaded), or null offline. */
export async function publishedSnapshot(): Promise<string | null> {
  try {
    const res = await fetch(DATA ? `${DATA}/meta.json` : apiUrl("/v1/meta"), { cache: "no-store" });
    if (!res.ok) return null;
    return ((await res.json()) as { snapshot_id?: string }).snapshot_id ?? null;
  } catch {
    return null;
  }
}
