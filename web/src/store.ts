// A small app-wide store: selection, the clock, filters, colour mode, quality and
// the simulator overlay. Components subscribe with useStore(selector).
import { useSyncExternalStore } from "react";
import type { SimulateResponse } from "./api";
import * as clocks from "./clock";
import { DEFAULT_FILTERS, PHONE_FILTERS, type ColorMode, type Filters } from "./orbits";

export interface Focus {
  conjunctionId: string;
  norads: [number, number];
  tca: number;
}

export type Quality = "high" | "balanced" | "battery";

export interface State {
  selected: number | null; // NORAD id
  focus: Focus | null; // selected close approach
  clock: clocks.Clock;
  filters: Filters;
  colorMode: ColorMode;
  quality: Quality;
  riskNorads: Set<number>; // objects in approaches above the operating point
  sim: { response: SimulateResponse; frame: number } | null;
}

const isPhone = typeof window !== "undefined" && window.matchMedia?.("(max-width: 700px)").matches;
const reducedMotion = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

// ?now=2026-11-01T12:00:00Z pins "the real time" (demos and tests); the clock still runs.
const nowOffset = (() => {
  if (typeof window === "undefined") return 0;
  const raw = new URLSearchParams(window.location.search).get("now");
  const t = raw ? Date.parse(raw) : Number.NaN;
  return Number.isFinite(t) ? t - Date.now() : 0;
})();

/** The real time (ms since the epoch), honouring ?now=. */
export function wallNow(): number {
  return Date.now() + nowOffset;
}

function initialQuality(): Quality {
  try {
    const saved = window.localStorage.getItem("perigee.quality");
    if (saved === "high" || saved === "balanced" || saved === "battery") return saved;
  } catch {
    // storage blocked: fall through to the automatic choice
  }
  if (typeof navigator === "undefined") return "balanced";
  const cores = navigator.hardwareConcurrency ?? 4;
  const memory = (navigator as Navigator & { deviceMemory?: number }).deviceMemory ?? 8;
  if (isPhone) return "battery";
  return cores >= 6 && memory >= 8 ? "high" : "balanced";
}

let state: State = {
  selected: null,
  focus: null,
  // Replaced with the real window once the snapshot time is known (initClock).
  clock: clocks.initialClock(Date.now(), Date.now(), reducedMotion),
  filters: isPhone ? PHONE_FILTERS : DEFAULT_FILTERS,
  colorMode: "type",
  quality: typeof window === "undefined" ? "balanced" : initialQuality(),
  riskNorads: new Set(),
  sim: null,
};
const listeners = new Set<() => void>();

export function getState(): State {
  return state;
}

export function setState(patch: Partial<State> | ((s: State) => Partial<State>)): void {
  const next = typeof patch === "function" ? patch(state) : patch;
  state = { ...state, ...next };
  listeners.forEach((l) => l());
}

export function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => listeners.delete(listener);
}

export function useStore<T>(selector: (s: State) => T): T {
  return useSyncExternalStore(subscribe, () => selector(state), () => selector(state));
}

export const prefersReducedMotion = reducedMotion;

// --- Clock actions --------------------------------------------------------------

export const time = {
  now: (): number => clocks.currentTime(state.clock, wallNow()),
  init: (snapshotMs: number) => setState({ clock: clocks.initialClock(snapshotMs, wallNow(), reducedMotion) }),
  goLive: () => setState((s) => ({ clock: clocks.goLive(s.clock, wallNow()) })),
  seek: (t: number, playing = false) => setState((s) => ({ clock: clocks.seek(s.clock, t, wallNow(), playing) })),
  setSpeed: (speed: number) => setState((s) => ({ clock: clocks.setSpeed(s.clock, speed, wallNow()) })),
  togglePlay: () => setState((s) => ({ clock: clocks.togglePlay(s.clock, wallNow()) })),
};

export function setQuality(quality: Quality): void {
  try {
    window.localStorage.setItem("perigee.quality", quality);
  } catch {
    // not remembered; still applied for this visit
  }
  setState({ quality });
}
