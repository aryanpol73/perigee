// The API, answered in the browser from the static files the daily job publishes
// (src/results/static_site.py). Production has no API server: the site and these
// files are served by the CDN, and this module gives every GET the same body the
// FastAPI app would (tests/fixtures/static-parity.json checks it, route by route).
// Values come from the same Python builders; here they are only filtered, sorted,
// paged and assembled, with the API's own rules.
import type { components } from "./api-types";

type S = components["schemas"];
type Conjunction = S["Conjunction"];
type ObjectSummary = S["ObjectSummary"];

export class ApiError extends Error {
  constructor(public status: number, message: string, public requestId?: string) {
    super(message);
  }
}

export type FetchJson = (url: string) => Promise<unknown>;

interface Index {
  format: number;
  run_id: string;
  source: S["Source"];
  disclaimer: string;
  max_data_age_hours: number | null;
  operating_probability: number | null;
  history_complete: boolean;
  conjunction_shards: number;
  orbit_file: string;
  has_model: boolean;
}

interface ConjunctionFile {
  rows: number;
  snapshot_us: number;
  objects: Record<string, [string, string, number]>;
  a: number[];
  b: number[];
  tca_us: number[];
  miss_distance_km: number[];
  relative_speed_kms: number[];
  risk_probability_estimate: (number | null)[];
  in_model_scope: number[];
}

interface ObjectFile {
  rows: number;
  columns: Record<string, unknown[]>;
}

const MAX_LIMIT = 500;
const SUMMARY_FIELDS = [
  "norad_id", "name", "object_type", "rcs_size", "owner", "regime", "perigee_km", "apogee_km", "inclination_deg", "epoch_age_days",
] as const;
const DETAIL_FIELDS = ["object_id", "launch_date", "ops_status_code", "period_min", "eccentricity", "epoch"] as const;
const TYPES = new Set(["PAY", "DEB", "R/B", "UNK"]);

/** FNV-1a (32-bit) of the UTF-8 key, mod shards: the same as static_site.shard_of. */
export function shardOf(key: string, shards: number): number {
  let h = 0x811c9dc5;
  for (const byte of new TextEncoder().encode(key)) {
    h = Math.imul(h ^ byte, 0x01000193) >>> 0;
  }
  return h % shards;
}

/** Microseconds since the epoch as pydantic writes a UTC datetime: 2026-10-31T14:49:51.497220Z. */
export function isoMicros(us: number): string {
  const ms = Math.floor(us / 1000);
  const base = new Date(ms).toISOString().slice(0, 19);
  const frac = us - Math.floor(us / 1_000_000) * 1_000_000;
  return frac === 0 ? `${base}Z` : `${base}.${String(frac).padStart(6, "0")}Z`;
}

function invalid(field: string, message: string): never {
  throw new ApiError(422, `query.${field}: ${message}`);
}

function num(q: URLSearchParams, key: string, lo: number, hi: number, opts: { gt?: boolean } = {}): number | null {
  const raw = q.get(key);
  if (raw === null || raw === "") return null;
  const v = Number(raw);
  if (!Number.isFinite(v)) invalid(key, "Input should be a valid number");
  if (opts.gt ? v <= lo : v < lo) invalid(key, `Input should be greater than${opts.gt ? "" : " or equal to"} ${lo}`);
  if (v > hi) invalid(key, `Input should be less than or equal to ${hi}`);
  return v;
}

function int(q: URLSearchParams, key: string, dflt: number, lo: number, hi: number): number {
  const v = num(q, key, lo, hi);
  if (v === null) return dflt;
  if (!Number.isInteger(v)) invalid(key, "Input should be a valid integer");
  return v;
}

function bool(q: URLSearchParams, key: string): boolean {
  const raw = (q.get(key) ?? "").toLowerCase();
  if (raw === "") return false;
  if (["true", "1", "yes", "on", "t", "y"].includes(raw)) return true;
  if (["false", "0", "no", "off", "f", "n"].includes(raw)) return false;
  return invalid(key, "Input should be a valid boolean");
}

function timeUs(q: URLSearchParams, key: string): number | null {
  const raw = q.get(key);
  if (!raw) return null;
  // A time without a zone is UTC, as the API reads it.
  const hasZone = /([zZ]|[+-]\d{2}:?\d{2})$/.test(raw);
  const ms = Date.parse(hasZone ? raw : `${raw}Z`);
  if (Number.isNaN(ms)) invalid(key, "Input should be a valid datetime");
  const frac = /\.(\d+)/.exec(raw)?.[1] ?? "";
  const extraUs = frac.length > 3 ? Number(frac.slice(3, 6).padEnd(3, "0")) : 0;
  return ms * 1000 + extraUs;
}

export function createStaticApi(base: string, fetchJson: FetchJson, now: () => number = Date.now) {
  const root = base.replace(/\/$/, "");
  const files = new Map<string, Promise<unknown>>();
  const load = <T,>(name: string): Promise<T> => {
    if (!files.has(name)) {
      const p = fetchJson(`${root}/${name}`);
      p.catch(() => files.delete(name));
      files.set(name, p);
    }
    return files.get(name) as Promise<T>;
  };
  const index = () => load<Index>("index.json");

  // --- Conjunctions -------------------------------------------------------------
  type Loaded = { f: ConjunctionFile; items: Conjunction[]; orders: Record<string, Int32Array>; byId: Map<string, number> };
  let conj: Promise<Loaded> | null = null;
  const conjunctions = () =>
    (conj ??= Promise.all([load<ConjunctionFile>("conjunctions.json"), index(), load<S["Meta"]>("meta.json")]).then(([f, ix, meta]) => {
      const op = ix.operating_probability;
      const items: Conjunction[] = new Array(f.rows);
      const byId = new Map<string, number>();
      for (let i = 0; i < f.rows; i++) {
        const a = f.a[i];
        const b = f.b[i];
        const [nameA, typeA, ageA] = f.objects[a];
        const [nameB, typeB, ageB] = f.objects[b];
        const p = f.risk_probability_estimate[i];
        const id = `${meta.snapshot_id}_${a}_${b}_${Math.floor(f.tca_us[i] / 1_000_000)}`;
        byId.set(id, i);
        items[i] = {
          id,
          tca: isoMicros(f.tca_us[i]),
          hours_to_tca: (f.tca_us[i] - f.snapshot_us) / 3.6e9,
          miss_distance_km: f.miss_distance_km[i],
          relative_speed_kms: f.relative_speed_kms[i],
          risk_probability_estimate: p,
          in_model_scope: f.in_model_scope[i] === 1,
          above_operating_point: p === null || op === null ? null : p >= op,
          estimate_status: p === null ? "none" : ix.history_complete ? "standard" : "provisional",
          objects: [
            { norad_id: a, name: nameA, object_type: typeA, epoch_age_days: ageA },
            { norad_id: b, name: nameB, object_type: typeB, epoch_age_days: ageB },
          ],
        } as Conjunction;
      }
      // The API's presorted orders (src/api/data.py): ties broken by TCA, then bundle order.
      const pos = Array.from({ length: f.rows }, (_, i) => i);
      const order = (cmp: (i: number, j: number) => number) => Int32Array.from([...pos].sort((i, j) => cmp(i, j) || i - j));
      const pkey = (i: number) => {
        const p = f.risk_probability_estimate[i];
        return p === null ? Infinity : -p;
      };
      const orders: Record<string, Int32Array> = {
        probability: order((i, j) => pkey(i) - pkey(j) || f.tca_us[i] - f.tca_us[j]),
        tca: order((i, j) => f.tca_us[i] - f.tca_us[j]),
        miss: order((i, j) => f.miss_distance_km[i] - f.miss_distance_km[j] || f.tca_us[i] - f.tca_us[j]),
      };
      return { f, items, orders, byId };
    }));

  // --- Objects ------------------------------------------------------------------
  let objs: Promise<{ summaries: ObjectSummary[]; extra: Record<string, unknown>[]; byNorad: Map<number, number> }> | null = null;
  const objects = () =>
    (objs ??= load<ObjectFile>("objects.json").then((f) => {
      const summaries: ObjectSummary[] = [];
      const extra: Record<string, unknown>[] = [];
      const byNorad = new Map<number, number>();
      for (let i = 0; i < f.rows; i++) {
        const s: Record<string, unknown> = {};
        for (const k of SUMMARY_FIELDS) s[k] = f.columns[k][i];
        const e: Record<string, unknown> = {};
        for (const k of DETAIL_FIELDS) e[k] = f.columns[k][i];
        summaries.push(s as ObjectSummary);
        extra.push(e);
        byNorad.set(s.norad_id as number, i);
      }
      return { summaries, extra, byNorad };
    }));

  const listConjunctions = async (q: URLSearchParams): Promise<S["ConjunctionPage"]> => {
    const minP = num(q, "min_probability", 0, 1);
    const maxMiss = num(q, "max_miss_km", 0, 50, { gt: true });
    const from = timeUs(q, "tca_from");
    const to = timeUs(q, "tca_to");
    const type = q.get("object_type");
    if (type !== null && !TYPES.has(type)) invalid("object_type", "Input should be 'PAY', 'DEB', 'R/B' or 'UNK'");
    const norad = q.get("norad_id") ? int(q, "norad_id", 0, 1, Number.MAX_SAFE_INTEGER) : null;
    const scoredOnly = bool(q, "scored_only");
    const sort = q.get("sort") ?? "probability";
    if (!["probability", "tca", "miss"].includes(sort)) invalid("sort", "Input should be 'probability', 'tca' or 'miss'");
    const limit = int(q, "limit", 50, 1, MAX_LIMIT);
    const offset = int(q, "offset", 0, 0, 1_000_000);
    const [{ f, items, orders }, ix] = await Promise.all([conjunctions(), index()]);
    const keep = (i: number) => {
      const p = f.risk_probability_estimate[i];
      const it = items[i];
      if (minP !== null && !(p !== null && p >= minP)) return false;
      if (maxMiss !== null && !(f.miss_distance_km[i] <= maxMiss)) return false;
      if (from !== null && f.tca_us[i] < from) return false;
      if (to !== null && f.tca_us[i] > to) return false;
      if (type !== null && it.objects[0].object_type !== type && it.objects[1].object_type !== type) return false;
      if (norad !== null && f.a[i] !== norad && f.b[i] !== norad) return false;
      if (scoredOnly && p === null) return false;
      return true;
    };
    const selected = Array.from(orders[sort], Number).filter(keep);
    return {
      source: ix.source,
      total: selected.length,
      limit,
      offset,
      items: selected.slice(offset, offset + limit).map((i) => items[i]),
      disclaimer: ix.disclaimer,
    };
  };

  const conjunctionDetail = async (id: string): Promise<S["ConjunctionDetail"]> => {
    const [{ items, byId }, ix, { summaries, byNorad }] = await Promise.all([conjunctions(), index(), objects()]);
    const i = byId.get(id);
    if (i === undefined) throw new ApiError(404, "No such conjunction in the latest run.");
    const shard = String(shardOf(id, ix.conjunction_shards)).padStart(2, "0");
    const extra = (await load<Record<string, { top_reasons: S["Reason"][]; confidence_note: string }>>(`conjunction-extra/${shard}.json`))[id];
    const item = items[i];
    return {
      ...item,
      source: ix.source,
      object_details: item.objects.map((o) => summaries[byNorad.get(o.norad_id)!]),
      top_reasons: extra.top_reasons,
      confidence_note: extra.confidence_note,
      disclaimer: ix.disclaimer,
    } as S["ConjunctionDetail"];
  };

  const listObjects = async (q: URLSearchParams): Promise<S["ObjectPage"]> => {
    const type = q.get("object_type");
    if (type !== null && !TYPES.has(type)) invalid("object_type", "Input should be 'PAY', 'DEB', 'R/B' or 'UNK'");
    const minAlt = num(q, "min_alt_km", 0, 500_000);
    const maxAlt = num(q, "max_alt_km", 0, 500_000);
    const raw = q.get("q");
    if (raw !== null && (raw.length < 1 || raw.length > 64)) invalid("q", "String should have between 1 and 64 characters");
    const limit = int(q, "limit", 50, 1, MAX_LIMIT);
    const offset = int(q, "offset", 0, 0, 1_000_000);
    const [{ summaries }, ix] = await Promise.all([objects(), index()]);
    const needle = raw?.trim() ?? "";
    const lower = needle.toLowerCase();
    const hits = summaries.filter(
      (o) =>
        (type === null || o.object_type === type) &&
        (minAlt === null || o.apogee_km >= minAlt) &&
        (maxAlt === null || o.perigee_km <= maxAlt) &&
        (!raw || String(o.norad_id) === needle || (o.name ?? "").toLowerCase().includes(lower)),
    );
    return { source: ix.source, total: hits.length, limit, offset, items: hits.slice(offset, offset + limit) };
  };

  const objectDetail = async (norad: number): Promise<S["ObjectDetail"]> => {
    const [{ summaries, extra, byNorad }, ix, { f, items, orders }] = await Promise.all([objects(), index(), conjunctions()]);
    const i = byNorad.get(norad);
    if (i === undefined) throw new ApiError(404, `No object ${norad} in this snapshot.`);
    const mine: Conjunction[] = [];
    for (const k of orders.tca) {
      if (f.a[k] === norad || f.b[k] === norad) {
        mine.push(items[k]);
        if (mine.length === MAX_LIMIT) break;
      }
    }
    return { ...summaries[i], source: ix.source, ...extra[i], conjunctions: mine } as S["ObjectDetail"];
  };

  const health = async (): Promise<S["Health"]> => {
    const [ix, meta] = await Promise.all([index(), load<S["Meta"]>("meta.json")]);
    const age = (now() - Date.parse(meta.snapshot_time)) / 3_600_000;
    const limit = ix.max_data_age_hours;
    return {
      status: limit === null || age <= limit ? "ok" : "stale",
      data_age_hours: Math.round(age * 100) / 100,
      max_data_age_hours: limit,
      snapshot_id: meta.snapshot_id,
      model_version: meta.model_version,
      code_commit: (import.meta.env?.VITE_COMMIT as string | undefined) || null,
    };
  };

  return {
    /** The body the API would return for GET `path` (throws ApiError like the API's error responses). */
    async get<T>(path: string): Promise<T> {
      const url = new URL(path, "http://static.invalid");
      const q = url.searchParams;
      const p = decodeURIComponent(url.pathname);
      let m: RegExpMatchArray | null;
      if (p === "/v1/meta") return (await load("meta.json")) as T;
      if (p === "/v1/health") return (await health()) as T;
      if (p === "/v1/shells") return (await load("shells.json")) as T;
      if (p === "/v1/shells/history") return (await load("shells-history.json")) as T;
      if (p === "/v1/model") {
        if (!(await index()).has_model) throw new ApiError(404, "No model was trained for this run; conjunctions are shown without estimates.");
        return (await load("model.json")) as T;
      }
      if (p === "/v1/conjunctions") return (await listConjunctions(q)) as T;
      if ((m = p.match(/^\/v1\/conjunctions\/(.+)$/))) return (await conjunctionDetail(m[1])) as T;
      if (p === "/v1/objects") return (await listObjects(q)) as T;
      if ((m = p.match(/^\/v1\/objects\/(\d+)$/))) return (await objectDetail(Number(m[1]))) as T;
      if ((m = p.match(/^\/v1\/orbits\/([\w.-]+)$/))) {
        const ix = await index();
        if (m[1].replace(/\.gz$/, "") !== ix.orbit_file) throw new ApiError(404, "Unknown orbit file; ask /v1/meta for the current one.");
        return (await load(`orbits/${ix.orbit_file}`)) as T;
      }
      throw new ApiError(404, "Not Found");
    },
    /** index.json (format, source, simulator defaults). */
    index: <T = Index,>() => index() as Promise<T>,
    /** Files the simulator worker needs (the catalog in the simulator's shape). */
    simCatalog: () => load<{ snapshot_time: string; columns: Record<string, unknown[]> }>("sim-catalog.json"),
  };
}

export type StaticApi = ReturnType<typeof createStaticApi>;
