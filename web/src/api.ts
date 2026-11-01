// Typed client for the read-only API, with caching and retry. Types come from the
// API's OpenAPI schema (npm run gen:api), so the two cannot drift silently.
import { useEffect, useState } from "react";
import type { components } from "./api-types";

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

const BASE = import.meta.env.VITE_API_BASE ?? "";
const cache = new Map<string, Promise<unknown>>();

export class ApiError extends Error {
  constructor(public status: number, message: string, public requestId?: string) {
    super(message);
  }
}

async function request<T>(path: string, init?: RequestInit, attempts = 3): Promise<T> {
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
