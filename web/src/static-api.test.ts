import { describe, expect, it } from "vitest";
import parity from "../../tests/fixtures/static-parity.json?raw";
import { ApiError, createStaticApi, isoMicros, shardOf } from "./static-api";

// tests/fixtures/static is the fixture bundle exported by src/results/static_site.py;
// static-parity.json holds what the FastAPI app returned for each request
// (tests/test_static_site.py writes both; UPDATE_STATIC_FIXTURE=1 refreshes them).
const files = import.meta.glob("../../tests/fixtures/static/**/*.json", { eager: true, query: "?raw", import: "default" }) as Record<string, string>;
const readJson = (url: string) => {
  const text = files[`../../tests/fixtures/static/${url.replace(/^\/data\//, "")}`];
  return text === undefined ? Promise.reject(new ApiError(404, "Not Found")) : Promise.resolve(JSON.parse(text));
};
const cases: { path: string; status: number; body: unknown }[] = JSON.parse(parity);

describe("static site answers like the API", () => {
  const api = createStaticApi("/data", readJson);

  it("covers a wide set of requests", () => {
    expect(cases.length).toBeGreaterThan(300);
  });

  for (const c of cases) {
    it(`${c.status} ${c.path.slice(0, 120)}`, async () => {
      if (c.status === 404) {
        await expect(api.get(c.path)).rejects.toMatchObject({ status: 404 });
        return;
      }
      expect(await api.get(c.path)).toEqual(c.body);
    });
  }
});

describe("static site details", () => {
  it("rejects what the API rejects", async () => {
    const api = createStaticApi("/data", readJson);
    await expect(api.get("/v1/conjunctions?limit=501")).rejects.toBeInstanceOf(ApiError);
    await expect(api.get("/v1/conjunctions?sort=random")).rejects.toMatchObject({ status: 422 });
    await expect(api.get("/v1/objects?q=")).rejects.toMatchObject({ status: 422 }); // as the API
    await expect(api.get("/v1/conjunctions?scored_only=maybe")).rejects.toMatchObject({ status: 422 });
  });

  it("reports health from the data's age", async () => {
    const meta = await readJson("meta.json");
    const t = Date.parse((meta as { snapshot_time: string }).snapshot_time) + 5 * 3_600_000;
    const h = await createStaticApi("/data", readJson, () => t).get<{ status: string; data_age_hours: number }>("/v1/health");
    expect(h.data_age_hours).toBe(5);
    expect(h.status).toBe("ok"); // the fixture has no age limit
  });

  it("matches the Python shard hash and timestamp format", () => {
    expect(shardOf("", 2 ** 32)).toBe(0x811c9dc5);
    expect(shardOf("a", 2 ** 32)).toBe(0xe40c292c);
    expect(isoMicros(Date.UTC(2026, 9, 31, 14, 49, 51) * 1000 + 497_220)).toBe("2026-10-31T14:49:51.497220Z");
    expect(isoMicros(Date.UTC(2026, 9, 31, 14, 49, 51) * 1000)).toBe("2026-10-31T14:49:51Z");
  });
});
