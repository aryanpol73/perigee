// Typed client for the read-only API, with caching and retry. Types come from the
// API's OpenAPI schema (npm run gen:api), so the two cannot drift silently.
import { useEffect, useState } from "react";
import type { components } from "./api-types";
import { ApiError, createStaticApi } from "./static-api";
import { createStaticSimulator } from "./static-sim";

export type Schemas = components["schemas"];
export type Meta = Schemas["Meta"];
export type Conjunction = Schemas["Conjunction"];
export type ConjunctionPage = Schemas["ConjunctionPage"];
export type ConjunctionDetail = Schemas["ConjunctionDetail"];
export type ObjectDetail = Schemas["ObjectDetail"];
export type Shells = Schemas["Shells"];
export type ShellHistory = Schemas["ShellHistory"];
export type ModelSummary = Schemas["ModelSummary"];
export type SimulateRequest = Schemas["SimulateRequest"];
export type SimulateResponse = Schemas["SimulateResponse"];
export type Health = Schemas["Health"];

// Two ways to reach the data, chosen at build time:
//   VITE_DATA_BASE set (production, e.g. "/data"): the static files the daily job
//     publishes next to the site, answered in the browser (static-api.ts) and the
//     simulator run in the browser (static-sim.ts). No server.
//   otherwise: the FastAPI app (local development, through the dev proxy, or
//     VITE_API_BASE for an API on another origin).
const BASE = (import.meta.env.VITE_API_BASE ?? "").replace(/\/$/, "");
const DATA = (import.meta.env.VITE_DATA_BASE ?? "").replace(/\/$/, "");
const PYODIDE = import.meta.env.VITE_PYODIDE_BASE ?? "/pyodide/";

/** Absolute URL of an API path, for the rare call that does not go through get(). */
export const apiUrl = (path: string): string => `${BASE}${path}`;
const cache = new Map<string, Promise<unknown>>();

export { ApiError };

async function fetchJson(url: string, attempts = 3): Promise<unknown> {
  let last: unknown;
  for (let i = 0; i < attempts; i++) {
    try {
      const res = await fetch(url);
      if (res.ok) return await res.json();
      if (res.status === 404) throw new ApiError(404, "Not Found");
      last = new ApiError(res.status, res.statusText);
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) throw e;
      last = e;
    }
    await new Promise((r) => setTimeout(r, 500 * 2 ** i));
  }
  throw last;
}

const staticApi = DATA ? createStaticApi(DATA, fetchJson) : null;
const staticSimulate = staticApi ? createStaticSimulator(staticApi, PYODIDE) : null;

async function request<T>(path: string, init?: RequestInit, attempts = 3): Promise<T> {
  if (staticApi) {
    if (init?.method === "POST" && path === "/v1/simulate") return staticSimulate!.simulate<T>(JSON.parse(String(init.body)));
    return staticApi.get<T>(path);
  }
  let last: unknown;
  for (let i = 0; i < attempts; i++) {
    try {
      const res = await fetch(`${BASE}${path}`, init);
      if (res.ok) return (await res.json()) as T;
      const body = await res.json().catch(() => null);
      const err = new ApiError(res.status, body?.error?.message ?? res.statusText, body?.error?.request_id);
      // Client errors will not get better by retrying.
      if (res.status < 500 && res.status !== 429) throw err;
      last = err;
    } catch (e) {
      if (e instanceof ApiError && e.status < 500 && e.status !== 429) throw e;
      last = e;
    }
    await new Promise((r) => setTimeout(r, 500 * 2 ** i));
  }
  throw last;
}

export function get<T>(path: string): Promise<T> {
  if (!cache.has(path)) {
    const p = request<T>(path);
    p.catch(() => cache.delete(path));
    cache.set(path, p);
  }
  return cache.get(path) as Promise<T>;
}

export function post<T>(path: string, body: unknown): Promise<T> {
  return request<T>(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) }, 1);
}

/** On the static site, start the in-browser simulator ahead of its first run (no-op with the API). */
export function prepareSimulator(): void {
  staticSimulate?.prepare();
}

/** /v1/health, which answers 503 with a body when data is stale (the app shows it). */
export async function fetchHealth(): Promise<Health> {
  if (staticApi) return staticApi.get<Health>("/v1/health");
  return (await fetch(apiUrl("/v1/health"))).json() as Promise<Health>;
}

export function query(params: Record<string, string | number | boolean | undefined | null>): string {
  const q = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(String(v))}`)
    .join("&");
  return q ? `?${q}` : "";
}

export interface Loadable<T> {
  data?: T;
  error?: Error;
  loading: boolean;
}

/** Fetch on mount and whenever `path` changes; null path fetches nothing. */
export function useApi<T>(path: string | null): Loadable<T> {
  const [state, setState] = useState<Loadable<T>>({ loading: path !== null });
  useEffect(() => {
    if (path === null) {
      setState({ loading: false });
      return;
    }
    let live = true;
    setState((s) => ({ data: s.data, loading: true }));
    get<T>(path).then(
      (data) => live && setState({ data, loading: false }),
      (error: Error) => live && setState({ error, loading: false }),
    );
    return () => {
      live = false;
    };
  }, [path]);
  return state;
}
