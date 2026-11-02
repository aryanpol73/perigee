// The breakup simulator in the browser: Pyodide (Python compiled to WebAssembly)
// runs the same Python files the API runs (src/sim/service.py and the physics it
// imports), so the static site's simulator gives the API's answer. Pyodide and its
// numpy/pandas are served by the site itself (scripts/vendor-pyodide.mjs).
import { BOOT, RUN, SOURCES } from "./sim-python";

type Request =
  | { kind: "init"; pyodideUrl: string; catalog: { snapshot_time: string; columns: Record<string, unknown[]> } }
  | { kind: "run"; id: number; request: Record<string, unknown> };

interface Pyodide {
  loadPackage(names: string[]): Promise<void>;
  runPythonAsync(code: string): Promise<unknown>;
  FS: { mkdirTree(path: string): void; writeFile(path: string, data: string): void };
  globals: { set(name: string, value: unknown): void };
}

let ready: Promise<Pyodide> | null = null;

async function start(pyodideUrl: string, catalog: { snapshot_time: string; columns: Record<string, unknown[]> }): Promise<Pyodide> {
  const { loadPyodide } = (await import(/* @vite-ignore */ `${pyodideUrl}pyodide.mjs`)) as {
    loadPyodide(opts: { indexURL: string }): Promise<Pyodide>;
  };
  const py = await loadPyodide({ indexURL: pyodideUrl });
  await py.loadPackage(["numpy", "pandas"]);
  for (const dir of ["/sim/src/orbits", "/sim/src/risk", "/sim/src/sim"]) py.FS.mkdirTree(dir);
  for (const [path, text] of Object.entries(SOURCES)) py.FS.writeFile(`/sim/${path}`, text);
  py.globals.set("catalog_json", JSON.stringify(catalog));
  await py.runPythonAsync(BOOT);
  return py;
}

self.onmessage = async (event: MessageEvent<Request>) => {
  const msg = event.data;
  if (msg.kind === "init") {
    ready ??= start(msg.pyodideUrl, msg.catalog);
    try {
      await ready;
      self.postMessage({ kind: "ready" });
    } catch (e) {
      ready = null;
      self.postMessage({ kind: "error", id: -1, status: 503, message: `The simulator could not start: ${(e as Error).message}` });
    }
    return;
  }
  if (msg.kind === "run") {
    try {
      const py = await ready!;
      py.globals.set("request_json", JSON.stringify(msg.request));
      const out = (await py.runPythonAsync(RUN)) as string;
      const parsed = JSON.parse(out) as { ok?: Record<string, unknown>; invalid?: string };
      if (parsed.invalid) self.postMessage({ kind: "error", id: msg.id, status: 422, message: parsed.invalid });
      else self.postMessage({ kind: "result", id: msg.id, response: parsed.ok });
    } catch (e) {
      self.postMessage({ kind: "error", id: msg.id, status: 500, message: `The simulation failed: ${(e as Error).message}` });
    }
  }
};
