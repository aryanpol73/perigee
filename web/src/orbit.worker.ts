// Propagates every object with satellite.js off the main thread. The UI thread
// only moves points. Positions are Earth-fixed (ECEF), metres, as Cesium expects.
import { eciToEcf, gstime, json2satrec, propagate, type SatRec } from "satellite.js";
import type { Omm } from "./orbits";

type Request =
  | { kind: "load"; records: Omm[] }
  | { kind: "positions"; id: number; time: number }
  | { kind: "path"; id: number; norad: number; time: number; samples: number };

let satrecs: (SatRec | null)[] = [];
let norads: number[] = [];

function ecef(sat: SatRec, date: Date, gmst: number, out: Float64Array, k: number): boolean {
  const pv = propagate(sat, date);
  const p = pv?.position;
  if (!p || typeof p === "boolean") {
    out[k] = out[k + 1] = out[k + 2] = Number.NaN;
    return false;
  }
  const f = eciToEcf(p, gmst);
  out[k] = f.x * 1000;
  out[k + 1] = f.y * 1000;
  out[k + 2] = f.z * 1000;
  return true;
}

self.onmessage = (event: MessageEvent<Request>) => {
  const msg = event.data;
  if (msg.kind === "load") {
    satrecs = msg.records.map((r) => {
      try {
        return json2satrec(r as never);
      } catch {
        return null;
      }
    });
    norads = msg.records.map((r) => r.NORAD_CAT_ID);
    self.postMessage({ kind: "loaded", count: satrecs.length });
    return;
  }
  if (msg.kind === "positions") {
    const date = new Date(msg.time);
    const gmst = gstime(date);
    const out = new Float64Array(satrecs.length * 3);
    let failed = 0;
    satrecs.forEach((s, i) => {
      if (!s || !ecef(s, date, gmst, out, i * 3)) {
        out[i * 3] = Number.NaN;
        failed++;
      }
    });
    self.postMessage({ kind: "positions", id: msg.id, time: msg.time, positions: out, failed }, { transfer: [out.buffer] });
    return;
  }
  if (msg.kind === "path") {
    // One revolution drawn at a single Earth rotation angle, so the path is the
    // closed orbit (inertial) as seen at this moment rather than a ground spiral.
    const i = norads.indexOf(msg.norad);
    const sat = i >= 0 ? satrecs[i] : null;
    const out = new Float64Array(msg.samples * 3);
    if (sat) {
      const periodMs = ((2 * Math.PI) / sat.no) * 60_000;
      const gmst = gstime(new Date(msg.time));
      for (let k = 0; k < msg.samples; k++) {
        ecef(sat, new Date(msg.time + (k / (msg.samples - 1)) * periodMs), gmst, out, k * 3);
      }
    }
    self.postMessage({ kind: "path", id: msg.id, norad: msg.norad, positions: out }, { transfer: [out.buffer] });
  }
};
