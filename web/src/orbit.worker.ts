// Propagates objects with satellite.js off the main thread. The UI thread only
// moves points. Several of these workers run side by side, each owning one slice of
// the catalog, so a sample of every object comes back in a fraction of the time
// (that is what keeps dragging the time slider responsive). Positions are
// Earth-fixed (ECEF) in metres, as Cesium expects, with Earth-fixed velocities (m/s)
// so the globe can glide smoothly between samples.
import { eciToEcf, gstime, json2satrec, propagate, type SatRec } from "satellite.js";
import type { Omm } from "./orbits";

type Request =
  | { kind: "load"; records: Omm[]; part: number }
  | { kind: "sample"; id: number; time: number }
  | { kind: "path"; id: number; norad: number; time: number; samples: number }
  | { kind: "trail"; id: number; norads: number[]; time: number; minutes: number; samples: number };

const EARTH_ROTATION = 7.2921158553e-5; // rad/s

let satrecs: (SatRec | null)[] = [];
let index = new Map<number, number>();
let part = 0;

/** Position (m) and velocity (m/s) in the Earth-fixed frame into out[k..k+5]. */
function stateEcef(sat: SatRec, date: Date, gmst: number, out: Float64Array, k: number): boolean {
  const pv = propagate(sat, date);
  const p = pv?.position;
  const v = pv?.velocity;
  if (!p || !v || typeof p === "boolean" || typeof v === "boolean") {
    out[k] = Number.NaN;
    return false;
  }
  const f = eciToEcf(p, gmst);
  // Rotate the inertial velocity into the Earth-fixed frame, then remove Earth's rotation.
  const c = Math.cos(gmst);
  const s = Math.sin(gmst);
  const vx = v.x * c + v.y * s + EARTH_ROTATION * f.y;
  const vy = -v.x * s + v.y * c - EARTH_ROTATION * f.x;
  out[k] = f.x * 1000;
  out[k + 1] = f.y * 1000;
  out[k + 2] = f.z * 1000;
  out[k + 3] = vx * 1000;
  out[k + 4] = vy * 1000;
  out[k + 5] = v.z * 1000;
  return true;
}

function positionEcef(sat: SatRec, date: Date, gmst: number, out: Float64Array, k: number): void {
  const pv = propagate(sat, date);
  const p = pv?.position;
  if (!p || typeof p === "boolean") {
    out[k] = out[k + 1] = out[k + 2] = Number.NaN;
    return;
  }
  const f = eciToEcf(p, gmst);
  out[k] = f.x * 1000;
  out[k + 1] = f.y * 1000;
  out[k + 2] = f.z * 1000;
}

self.onmessage = (event: MessageEvent<Request>) => {
  const msg = event.data;
  if (msg.kind === "load") {
    part = msg.part;
    satrecs = msg.records.map((r) => {
      try {
        return json2satrec(r as never);
      } catch {
        return null;
      }
    });
    index = new Map(msg.records.map((r, i) => [r.NORAD_CAT_ID, i]));
    self.postMessage({ kind: "loaded", part, count: satrecs.length });
    return;
  }
  if (msg.kind === "sample") {
    const started = performance.now();
    const date = new Date(msg.time);
    const gmst = gstime(date);
    const out = new Float64Array(satrecs.length * 6);
    let failed = 0;
    for (let i = 0; i < satrecs.length; i++) {
      const s = satrecs[i];
      if (!s || !stateEcef(s, date, gmst, out, i * 6)) {
        out[i * 6] = Number.NaN;
        failed++;
      }
    }
    const ms = performance.now() - started;
    self.postMessage({ kind: "sample", id: msg.id, part, time: msg.time, states: out, failed, ms }, { transfer: [out.buffer] });
    return;
  }
  if (msg.kind === "path") {
    // One revolution drawn at a single Earth rotation angle, so the path is the
    // closed orbit (inertial) as seen at this moment rather than a ground spiral.
    const i = index.get(msg.norad);
    const sat = i === undefined ? null : satrecs[i];
    const out = new Float64Array(msg.samples * 3);
    if (sat) {
      const periodMs = ((2 * Math.PI) / sat.no) * 60_000;
      const gmst = gstime(new Date(msg.time));
      for (let k = 0; k < msg.samples; k++) {
        positionEcef(sat, new Date(msg.time + (k / (msg.samples - 1)) * periodMs), gmst, out, k * 3);
      }
    }
    self.postMessage({ kind: "path", id: msg.id, norad: msg.norad, positions: out }, { transfer: [out.buffer] });
    return;
  }
  if (msg.kind === "trail") {
    // Where each object was over the last `minutes`, in the Earth-fixed frame at each moment.
    const out = new Float64Array(msg.norads.length * msg.samples * 3);
    msg.norads.forEach((norad, n) => {
      const i = index.get(norad);
      const sat = i === undefined ? null : satrecs[i];
      for (let k = 0; k < msg.samples; k++) {
        const t = msg.time - (1 - k / (msg.samples - 1)) * msg.minutes * 60_000;
        const date = new Date(t);
        const at = (n * msg.samples + k) * 3;
        if (sat) positionEcef(sat, date, gstime(date), out, at);
        else out[at] = Number.NaN;
      }
    });
    self.postMessage({ kind: "trail", id: msg.id, norads: msg.norads, samples: msg.samples, positions: out }, { transfer: [out.buffer] });
  }
};
