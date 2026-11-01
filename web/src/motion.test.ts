import { describe, expect, it } from "vitest";
import { eciToEcf, gstime, json2satrec, propagate } from "satellite.js";
import reference from "./__fixtures__/sgp4_reference.json";
import { Motion, interpolate, type Sample } from "./motion";

const W = 7.2921158553e-5;

// Earth-fixed position and velocity for one record, the same way the worker computes them.
function sampleOf(records: object[], time: number): Sample {
  const states = new Float64Array(records.length * 6);
  const date = new Date(time);
  const g = gstime(date);
  records.forEach((r, i) => {
    const pv = propagate(json2satrec(r as never), date)!;
    const p = pv.position as { x: number; y: number; z: number };
    const v = pv.velocity as { x: number; y: number; z: number };
    const f = eciToEcf(p, g);
    const c = Math.cos(g);
    const s = Math.sin(g);
    states.set(
      [f.x * 1e3, f.y * 1e3, f.z * 1e3, (v.x * c + v.y * s + W * f.y) * 1e3, (-v.x * s + v.y * c - W * f.x) * 1e3, v.z * 1e3],
      i * 6,
    );
  });
  return { time, states };
}

const leo = reference.cases.slice(0, 10).map((c) => c.record);
const t0 = new Date(reference.cases[0].points[0].time).getTime();

describe("smooth motion", () => {
  it("is exact at the samples", () => {
    const a = sampleOf(leo, t0);
    const b = sampleOf(leo, t0 + 60_000);
    const out = new Float64Array(leo.length * 3);
    interpolate(a, b, t0, out);
    expect(out[0]).toBeCloseTo(a.states[0], 6);
    interpolate(a, b, t0 + 60_000, out);
    expect(out[3]).toBeCloseTo(b.states[6], 6);
  });

  // Mid-way between samples the curve must stay on the orbit: within 0.5 km for samples
  // 6 minutes apart (3600x speed with samples every 0.1 s real time) and within 10 m for
  // samples 1 s apart (live).
  for (const [gapS, toleranceKm] of [
    [360, 0.5],
    [1, 0.01],
  ] as const) {
    it(`stays on the orbit between samples ${gapS} s apart`, () => {
      const a = sampleOf(leo, t0);
      const b = sampleOf(leo, t0 + gapS * 1000);
      const mid = t0 + (gapS * 1000) / 2;
      const truth = sampleOf(leo, mid);
      const out = new Float64Array(leo.length * 3);
      interpolate(a, b, mid, out);
      for (let i = 0; i < leo.length; i++) {
        const d = Math.hypot(out[i * 3] - truth.states[i * 6], out[i * 3 + 1] - truth.states[i * 6 + 1], out[i * 3 + 2] - truth.states[i * 6 + 2]);
        expect(d / 1000).toBeLessThan(toleranceKm);
      }
    });
  }

  it("asks for samples ahead of the clock, and starts over after a seek", () => {
    const m = new Motion(leo.length);
    expect(m.next(t0, 1_000)).toBe(t0);
    m.add(sampleOf(leo, t0));
    expect(m.next(t0, 1_000)).toBe(t0 + 1_000);
    m.add(sampleOf(leo, t0 + 1_000));
    expect(m.next(t0 + 100, 1_000)).toBe(t0 + 2_000);
    const far = t0 + 86_400_000;
    expect(m.next(far, 1_000)).toBe(far);
    const out = new Float64Array(leo.length * 3);
    expect(m.positions(t0 + 500, out)).toBe(true);
  });

  it("paused needs exactly one sample at the shown time", () => {
    const m = new Motion(leo.length);
    m.add(sampleOf(leo, t0));
    expect(m.next(t0, 0)).toBeNull();
    expect(m.next(t0 + 5, 0)).toBe(t0 + 5);
  });
});
