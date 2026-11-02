// Screens of the mobile app live in the URL hash, like the dashboard's tabs, so links,
// notifications and the Android back button work:
//   #/globe  #/conjunctions  #/object[/<norad>]  #/risk  #/simulator  #/settings  #/method
// Back from any screen returns to the globe, then leaves the app, as in native apps:
// leaving the globe adds a history entry, moving between other screens replaces it,
// and each entry records how many steps above the globe it is (perigeeDepth), so the
// Globe button can go back to it rather than stack another copy.
import { useCallback, useEffect, useRef, useState } from "react";

export type Tab = "globe" | "conjunctions" | "object" | "risk" | "settings";

export interface Route {
  tab: Tab;
  /** risk: "simulator"; settings: "method"; object: a NORAD id. */
  sub: string | null;
}

export function parseRoute(hash: string): Route {
  const [head = "", sub = null] = hash.replace(/^#\/?/, "").split("/");
  if (head === "simulator") return { tab: "risk", sub: "simulator" };
  if (head === "method") return { tab: "settings", sub: "method" };
  if (head === "conjunctions" || head === "object" || head === "risk" || head === "settings") return { tab: head, sub: sub || null };
  return { tab: "globe", sub: null };
}

export function useRoute(): Route {
  const [route, setRoute] = useState(() => parseRoute(window.location.hash));
  useEffect(() => {
    const sync = () => setRoute(parseRoute(window.location.hash));
    window.addEventListener("hashchange", sync);
    return () => window.removeEventListener("hashchange", sync);
  }, []);
  return route;
}

// A sheet that closes by itself removes its history entry with history.back(), which
// finishes later: navigation waits for it, or it would undo the navigation.
let pendingBack: Promise<void> | null = null;

function popEntry(): void {
  pendingBack = new Promise((resolve) => {
    let done = false;
    const finish = () => {
      if (done) return;
      done = true;
      window.removeEventListener("popstate", finish);
      pendingBack = null;
      resolve();
    };
    window.addEventListener("popstate", finish);
    window.history.back();
    window.setTimeout(finish, 400);
  });
}

/**
 * Called once as the app starts. The first entry is always the globe: opened on
 * another screen (a link, a notification), the globe goes underneath it, so Back
 * and the Globe button lead there. It also drops the state a reload keeps, whose
 * depth (or open sheet) belonged to the page before.
 */
export function startHistory(): void {
  const { pathname, search, hash } = window.location;
  if (parseRoute(hash).tab === "globe") {
    window.history.replaceState({ perigeeDepth: 0 }, "");
    return;
  }
  window.history.replaceState({ perigeeDepth: 0 }, "", `${pathname}${search}#/globe`);
  window.history.pushState({ perigeeDepth: 1 }, "", `${pathname}${search}${hash}`);
}

const depthOf = (state: unknown): number => Number((state as { perigeeDepth?: number } | null)?.perigeeDepth) || 0;

export async function navigate(path: string): Promise<void> {
  // A sheet closed in the same tap removes its entry once React has re-rendered.
  await new Promise((resolve) => window.setTimeout(resolve, 0));
  if (pendingBack) await pendingBack;
  const target = `#/${path}`;
  if (window.location.hash === target) return;
  const state = window.history.state ?? {};
  const depth = depthOf(state);
  if (parseRoute(target).tab === "globe" && depth > 0) {
    window.history.go(-depth); // the first entry, which is the globe (startHistory)
    return;
  }
  go(target, parseRoute(window.location.hash).tab === "globe");
}

function go(target: string, push: boolean): void {
  const state = window.history.state ?? {};
  const depth = depthOf(state);
  const oldURL = window.location.href;
  if (push) window.history.pushState({ perigeeDepth: depth + 1 }, "", target);
  else {
    // Leaving a screen with a sheet or page open: this entry becomes the new screen, so it
    // no longer belongs to the sheet (whose cleanup would otherwise go back to it).
    const { perigeeSheet: _sheet, ...rest } = state as Record<string, unknown>;
    window.history.replaceState(rest, "", target);
  }
  window.dispatchEvent(new HashChangeEvent("hashchange", { oldURL, newURL: window.location.href }));
}

/**
 * While `open`, the back button closes the sheet (or page) instead of leaving the
 * screen. Close buttons call the returned function, which goes back through the same
 * history entry, so no stale entry is left behind.
 */
export function useBackClose(open: boolean, close: () => void): () => void {
  const latest = useRef(close);
  const entry = useRef<string | null>(null);
  useEffect(() => {
    latest.current = close;
  });
  useEffect(() => {
    if (!open) return;
    const marker = `${Date.now()}-${Math.random()}`;
    const state = window.history.state ?? {};
    window.history.pushState({ ...state, perigeeSheet: marker, perigeeDepth: depthOf(state) + 1 }, "");
    entry.current = marker;
    let popped = false;
    const onPop = () => {
      popped = true;
      latest.current();
    };
    window.addEventListener("popstate", onPop);
    return () => {
      window.removeEventListener("popstate", onPop);
      entry.current = null;
      if (!popped && window.history.state?.perigeeSheet === marker) popEntry();
    };
  }, [open]);
  return useCallback(() => {
    if (entry.current && window.history.state?.perigeeSheet === entry.current) window.history.back();
    else latest.current();
  }, []);
}
