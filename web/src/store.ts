// A small app-wide store: selection, simulation time, filters, colour mode, and
// the simulator overlay. Components subscribe with useStore(selector).
import { useSyncExternalStore } from "react";
import type { SimulateResponse } from "./api";
import { DEFAULT_FILTERS, PHONE_FILTERS, type ColorMode, type Filters } from "./orbits";

export interface Focus {
  conjunctionId: string;
  norads: [number, number];
  tca: number;
}

export interface State {
  selected: number | null; // NORAD id
  focus: Focus | null; // selected close approach
  time: number; // ms since epoch, simulation clock
  playing: boolean;
  speed: number; // simulated seconds per real second
  filters: Filters;
  colorMode: ColorMode;
  riskNorads: Set<number>; // objects in approaches above the operating point
  sim: { response: SimulateResponse; frame: number } | null;
}

const isPhone = typeof window !== "undefined" && window.matchMedia?.("(max-width: 700px)").matches;
const reducedMotion = typeof window !== "undefined" && window.matchMedia?.("(prefers-reduced-motion: reduce)").matches;

let state: State = {
  selected: null,
  focus: null,
  time: Date.now(),
  playing: !reducedMotion,
  speed: 60,
  filters: isPhone ? PHONE_FILTERS : DEFAULT_FILTERS,
  colorMode: "type",
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
