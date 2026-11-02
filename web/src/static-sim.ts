// POST /v1/simulate for the static site: validates the request like the API's
// schema, fills its defaults, and runs the simulator in a worker (sim.worker.ts).
// The worker, Pyodide and numpy/pandas load only when someone runs a simulation.
import { ApiError, type StaticApi } from "./static-api";

interface Schema {
  properties: Record<string, { type?: string; minimum?: number; maximum?: number; enum?: string[]; minItems?: number; maxItems?: number }>;
  additionalProperties?: boolean;
}

interface IndexSim {
  simulate_defaults: Record<string, unknown>;
  simulate_schema: Schema;
  sim_label: string;
}

function validate(body: Record<string, unknown>, schema: Schema): void {
  for (const key of Object.keys(body)) {
    if (!(key in schema.properties)) throw new ApiError(422, `body.${key}: Extra inputs are not permitted`);
  }
  for (const [key, rule] of Object.entries(schema.properties)) {
    const v = body[key];
    if (v === undefined) continue;
    if (rule.enum && !rule.enum.includes(v as string)) throw new ApiError(422, `body.${key}: Input should be ${rule.enum.map((e) => `'${e}'`).join(" or ")}`);
    if (rule.type === "number" || rule.type === "integer") {
      if (typeof v !== "number" || !Number.isFinite(v) || (rule.type === "integer" && !Number.isInteger(v))) {
        throw new ApiError(422, `body.${key}: Input should be a valid ${rule.type}`);
      }
      if (rule.minimum !== undefined && v < rule.minimum) throw new ApiError(422, `body.${key}: Input should be greater than or equal to ${rule.minimum}`);
      if (rule.maximum !== undefined && v > rule.maximum) throw new ApiError(422, `body.${key}: Input should be less than or equal to ${rule.maximum}`);
    }
    if (rule.type === "array") {
      if (!Array.isArray(v)) throw new ApiError(422, `body.${key}: Input should be a valid list`);
      if (rule.minItems !== undefined && v.length < rule.minItems) throw new ApiError(422, `body.${key}: List should have at least ${rule.minItems} item`);
      if (rule.maxItems !== undefined && v.length > rule.maxItems) throw new ApiError(422, `body.${key}: List should have at most ${rule.maxItems} items`);
    }
  }
}

export function createStaticSimulator(api: StaticApi, pyodideUrl: string) {
  let worker: Worker | null = null;
  let started: Promise<void> | null = null;
  let next = 0;
  const pending = new Map<number, { resolve: (v: unknown) => void; reject: (e: Error) => void }>();

  const boot = () =>
    (started ??= (async () => {
      const catalog = await api.simCatalog();
      worker = new Worker(new URL("./sim.worker.ts", import.meta.url), { type: "module" });
      await new Promise<void>((resolve, reject) => {
        worker!.onmessage = (e: MessageEvent) => {
          const m = e.data;
          if (m.kind === "ready") resolve();
          else if (m.kind === "error" && m.id === -1) reject(new ApiError(m.status, m.message));
          else if (m.kind === "result") pending.get(m.id)?.resolve(m.response);
          else if (m.kind === "error") pending.get(m.id)?.reject(new ApiError(m.status, m.message));
          if (m.id !== undefined) pending.delete(m.id);
        };
        worker!.postMessage({ kind: "init", pyodideUrl, catalog });
      });
    })().catch((e) => {
      started = null;
      worker?.terminate();
      throw e;
    }));

  async function simulate<T>(body: Record<string, unknown>): Promise<T> {
    const ix = await api.index<IndexSim>();
    validate(body, ix.simulate_schema);
    const request = { ...ix.simulate_defaults, ...body };
    await boot();
    const id = ++next;
    const response = await new Promise<Record<string, unknown>>((resolve, reject) => {
      pending.set(id, { resolve: resolve as (v: unknown) => void, reject });
      worker!.postMessage({ kind: "run", id, request });
    });
    // As the API's schema writes it: its label first, and the time in UTC with a Z.
    return { label: ix.sim_label, ...response, collision_time: String(response.collision_time).replace(/\+00:00$/, "Z") } as T;
  }

  /** Start Python in the background (opening the simulator page), so the first run does not wait for it. */
  const prepare = () => void boot().catch(() => undefined);
  return { simulate, prepare };
}
