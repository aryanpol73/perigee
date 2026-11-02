// The simulator as the static site runs it (Pyodide, the vendored packages in
// public/pyodide) gives the API's answers (tests/fixtures/static-parity-simulate.json,
// written by tests/test_static_site.py). Pyodide's numpy is a different build, so
// numbers may differ in the last bits; everything else must be identical.
import { loadPyodide } from "pyodide";
import { describe, expect, it } from "vitest";
import catalogText from "../../tests/fixtures/static/sim-catalog.json?raw";
import parityText from "../../tests/fixtures/static-parity-simulate.json?raw";
import { BOOT, RUN, SOURCES } from "./sim-python";

const cases: { request: Record<string, unknown>; response: Record<string, unknown> }[] = JSON.parse(parityText);
const index = JSON.parse((import.meta.glob("../../tests/fixtures/static/index.json", { eager: true, query: "?raw", import: "default" }) as Record<string, string>)["../../tests/fixtures/static/index.json"]);

// A file path for Pyodide in Node (it reads the runtime and wheels from disk).
const localDir = (rel: string) => {
  const p = decodeURIComponent(new URL(rel, import.meta.url).pathname);
  return /^\/[A-Za-z]:/.test(p) ? p.slice(1) : p;
};

function close(actual: unknown, expected: unknown, path = "$"): void {
  if (typeof expected === "number" && typeof actual === "number") {
    const tol = 1e-9 * Math.max(1, Math.abs(expected));
    if (Math.abs(actual - expected) > tol) throw new Error(`${path}: ${actual} != ${expected}`);
    return;
  }
  if (Array.isArray(expected)) {
    if (!Array.isArray(actual) || actual.length !== expected.length) throw new Error(`${path}: length ${(actual as unknown[])?.length} != ${expected.length}`);
    expected.forEach((e, i) => close(actual[i], e, `${path}[${i}]`));
    return;
  }
  if (expected && typeof expected === "object") {
    const a = actual as Record<string, unknown>;
    const keys = Object.keys(expected);
    expect(Object.keys(a ?? {}).sort(), path).toEqual(keys.sort());
    keys.forEach((k) => close(a[k], (expected as Record<string, unknown>)[k], `${path}.${k}`));
    return;
  }
  expect(actual, path).toEqual(expected);
}

describe("browser simulator matches the API", () => {
  it(
    "runs the same simulations to the same results",
    async () => {
      const py = await loadPyodide({ indexURL: localDir("../public/pyodide/") });
      await py.loadPackage(["numpy", "pandas"]);
      for (const dir of ["/sim/src/orbits", "/sim/src/risk", "/sim/src/sim"]) py.FS.mkdirTree(dir);
      for (const [path, text] of Object.entries(SOURCES)) py.FS.writeFile(`/sim/${path}`, text);
      py.globals.set("catalog_json", catalogText);
      await py.runPythonAsync(BOOT);
      for (const c of cases) {
        py.globals.set("request_json", JSON.stringify({ ...index.simulate_defaults, ...c.request }));
        const out = JSON.parse((await py.runPythonAsync(RUN)) as string);
        const response = { label: index.sim_label, ...out.ok, collision_time: String(out.ok.collision_time).replace(/\+00:00$/, "Z") };
        close(response, c.response);
      }
    },
    120_000,
  );
});
