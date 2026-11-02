// Watched objects and notifications, without a server. When the app opens on a new
// snapshot it tells the user about it and about close approaches of the objects
// they watch; the service worker (public/sw.js) runs the "new data" check about once
// a day in the background where the browser allows it (installed apps on Android).
// Instant push alerts would need a server and are not part of this.
import { useSyncExternalStore } from "react";
import { get, type Conjunction, type Meta, type ObjectDetail } from "../api";
import { km } from "../format";

export interface NotifyPrefs {
  enabled: boolean; // the user turned notifications on (and the browser allowed them)
  newData: boolean;
  watched: boolean;
}

const PREFS = "perigee.notify";
const WATCH = "perigee.watch";
const LAST_RUN = "perigee.lastRun";
const DEFAULT_PREFS: NotifyPrefs = { enabled: false, newData: true, watched: true };
/** Watched-object alerts cover approaches this soon (the model scores up to 72 h). */
export const ALERT_HOURS = 72;
/** ... closer than this, or with an elevated risk estimate. */
export const ALERT_MISS_KM = 1;

function read<T>(key: string, fallback: T): T {
  try {
    const raw = window.localStorage.getItem(key);
    return raw ? (JSON.parse(raw) as T) : fallback;
  } catch {
    return fallback;
  }
}

function write(key: string, value: unknown): void {
  try {
    window.localStorage.setItem(key, JSON.stringify(value));
  } catch {
    // storage blocked: kept for this visit only
  }
}

// --- A small store for the watch list and preferences -----------------------------

let watch: number[] = typeof window === "undefined" ? [] : read<number[]>(WATCH, []);
let prefs: NotifyPrefs = typeof window === "undefined" ? DEFAULT_PREFS : { ...DEFAULT_PREFS, ...read<Partial<NotifyPrefs>>(PREFS, {}) };
const listeners = new Set<() => void>();
const emit = () => listeners.forEach((l) => l());
const subscribe = (l: () => void) => {
  listeners.add(l);
  return () => listeners.delete(l);
};

export function useWatchList(): number[] {
  return useSyncExternalStore(subscribe, () => watch, () => watch);
}

export function useNotifyPrefs(): NotifyPrefs {
  return useSyncExternalStore(subscribe, () => prefs, () => prefs);
}

export function toggleWatch(norad: number): void {
  watch = watch.includes(norad) ? watch.filter((n) => n !== norad) : [...watch, norad];
  write(WATCH, watch);
  emit();
  void syncWorkerState();
}

export function setNotifyPrefs(patch: Partial<NotifyPrefs>): void {
  prefs = { ...prefs, ...patch };
  write(PREFS, prefs);
  emit();
  void syncWorkerState();
}

export function notificationsSupported(): boolean {
  return typeof window !== "undefined" && "Notification" in window;
}

export function permission(): NotificationPermission | "unsupported" {
  return notificationsSupported() ? Notification.permission : "unsupported";
}

/** Asks the browser, then turns notifications on. False if refused or unsupported. */
export async function enableNotifications(): Promise<boolean> {
  if (!notificationsSupported()) return false;
  const answer = Notification.permission === "granted" ? "granted" : await Notification.requestPermission();
  if (answer !== "granted") {
    setNotifyPrefs({ enabled: false });
    return false;
  }
  setNotifyPrefs({ enabled: true });
  await registerBackgroundCheck();
  return true;
}

// The service worker reads the preferences from a cache (it has no localStorage).
async function syncWorkerState(): Promise<void> {
  if (typeof caches === "undefined") return;
  try {
    const state = { ...prefs, watchCount: watch.length, lastRun: read<string | null>(LAST_RUN, null) };
    await (await caches.open("perigee-state")).put("/__state", new Response(JSON.stringify(state), { headers: { "Content-Type": "application/json" } }));
  } catch {
    // no cache storage (private window): the background check stays off
  }
}

async function registerBackgroundCheck(): Promise<void> {
  try {
    const reg = await navigator.serviceWorker?.ready;
    const sync = (reg as ServiceWorkerRegistration & { periodicSync?: { register(tag: string, o: { minInterval: number }): Promise<void> } })
      ?.periodicSync;
    await sync?.register("perigee-new-data", { minInterval: 12 * 3_600_000 });
  } catch {
    // not allowed here (most browsers): the check runs when the app opens instead
  }
}

/** Shows a notification through the service worker (Android needs that), else directly. */
export async function notify(title: string, body: string, url = "/?app=1#/conjunctions", tag?: string): Promise<void> {
  if (permission() !== "granted") return;
  const options: NotificationOptions = { body, icon: "/icons/icon-192.png", badge: "/icons/badge-96.png", tag, data: { url } };
  const reg = await navigator.serviceWorker?.getRegistration().catch(() => undefined);
  if (reg) await reg.showNotification(title, options);
  else new Notification(title, options);
}

// --- What to tell the user ---------------------------------------------------------

export interface Alert {
  norad: number;
  title: string;
  body: string;
}

/** Upcoming approaches of one watched object worth a notification (soonest first). */
export function watchAlerts(o: ObjectDetail, now: number, operating: number | null): Alert[] {
  const soon = o.conjunctions.filter((c) => {
    const h = (new Date(c.tca).getTime() - now) / 3_600_000;
    const elevated = operating !== null && (c.risk_probability_estimate ?? -1) >= operating;
    return h >= 0 && h <= ALERT_HOURS && (c.miss_distance_km < ALERT_MISS_KM || elevated);
  });
  if (!soon.length) return [];
  const first = [...soon].sort((a, b) => a.tca.localeCompare(b.tca))[0];
  const other = (c: Conjunction) => c.objects.find((x) => x.norad_id !== o.norad_id) ?? c.objects[1];
  const hours = Math.max(0, Math.round((new Date(first.tca).getTime() - now) / 3_600_000));
  const more = soon.length > 1 ? ` (+${soon.length - 1} more in ${ALERT_HOURS} h)` : "";
  return [
    {
      norad: o.norad_id,
      title: `${o.name}: close approach in ${hours} h`,
      body: `Estimated ${km(first.miss_distance_km)} from ${other(first).name ?? other(first).norad_id}${more}. Estimate from public data; not for operational use.`,
    },
  ];
}

/**
 * Once per new snapshot: what changed since the last visit. The first visit only
 * remembers the snapshot. Returns the alerts it showed (for the in-app list).
 */
export async function checkOnOpen(meta: Meta, now: number): Promise<Alert[]> {
  const last = read<string | null>(LAST_RUN, null);
  if (last === meta.snapshot_id) return [];
  write(LAST_RUN, meta.snapshot_id);
  void syncWorkerState();
  if (last === null) return [];
  const alerts: Alert[] = [];
  if (prefs.watched && watch.length) {
    const details = await Promise.all(watch.map((n) => get<ObjectDetail>(`/v1/objects/${n}`).catch(() => null)));
    for (const d of details) if (d) alerts.push(...watchAlerts(d, now, meta.operating_probability ?? null));
  }
  if (prefs.enabled) {
    if (prefs.newData) {
      await notify("New orbit data in Perigee", `${meta.conjunction_count.toLocaleString("en-US")} close approaches estimated for the next 7 days.`, undefined, "perigee-new-data");
    }
    for (const a of alerts) await notify(a.title, a.body, `/?app=1#/object/${a.norad}`, `perigee-watch-${a.norad}`);
  }
  return alerts;
}
