// Smooth motion between worker samples. Each sample holds every object's
// Earth-fixed position and velocity at one simulated time (6 numbers per object).
// Every frame, positions come from cubic Hermite interpolation between the two
// samples around the current time: exact at the samples, matching velocity there,
// so points glide instead of jumping, even when samples are minutes of simulated
// time apart at high speed.

export interface Sample {
  time: number; // simulated ms since the epoch
  states: Float64Array; // [x, y, z, vx, vy, vz] per object, metres and m/s; NaN x = unavailable
}

/** Hermite-interpolate positions at time t between samples a and b into out (3 per object). */
export function interpolate(a: Sample, b: Sample, t: number, out: Float64Array): void {
  const dt = (b.time - a.time) / 1000; // seconds
  const s = dt > 0 ? Math.min(1, Math.max(0, (t - a.time) / (b.time - a.time))) : 0;
  const s2 = s * s;
  const s3 = s2 * s;
  const h00 = 2 * s3 - 3 * s2 + 1;
  const h10 = (s3 - 2 * s2 + s) * dt;
  const h01 = -2 * s3 + 3 * s2;
  const h11 = (s3 - s2) * dt;
  const A = a.states;
  const B = b.states;
  const n = out.length / 3;
  for (let i = 0; i < n; i++) {
    const k = i * 6;
    const o = i * 3;
    if (Number.isNaN(A[k]) || Number.isNaN(B[k])) {
      out[o] = Number.NaN;
      continue;
    }
    out[o] = h00 * A[k] + h10 * A[k + 3] + h01 * B[k] + h11 * B[k + 3];
    out[o + 1] = h00 * A[k + 1] + h10 * A[k + 4] + h01 * B[k + 1] + h11 * B[k + 4];
    out[o + 2] = h00 * A[k + 2] + h10 * A[k + 5] + h01 * B[k + 2] + h11 * B[k + 5];
  }
}

// A straight line leaves a curved orbit quickly (about 15 km after a minute in LEO),
// so beyond this the points hold where they are until the next sample arrives. While
// the time slider is dragged that means points wait for their real positions instead
// of shooting off along their velocity.
export const MAX_EXTRAPOLATE_MS = 60_000;

/** Positions at time t from one sample, moving along each velocity (short spans only). */
export function extrapolate(a: Sample, t: number, out: Float64Array): void {
  const dt = Math.max(-MAX_EXTRAPOLATE_MS, Math.min(MAX_EXTRAPOLATE_MS, t - a.time)) / 1000;
  const A = a.states;
  const n = out.length / 3;
  for (let i = 0; i < n; i++) {
    const k = i * 6;
    const o = i * 3;
    out[o] = A[k] + A[k + 3] * dt;
    out[o + 1] = A[k + 1] + A[k + 4] * dt;
    out[o + 2] = A[k + 2] + A[k + 5] * dt;
  }
}

/**
 * Keeps the last few samples and decides which simulated time to ask the worker for next.
 * `lead` is how far ahead of the current time (simulated ms) samples should reach.
 */
export class Motion {
  private samples: Sample[] = [];

  constructor(private readonly count: number) {}

  get ready(): boolean {
    return this.samples.length > 0;
  }

  add(sample: Sample): void {
    this.samples.push(sample);
    this.samples.sort((x, y) => x.time - y.time);
    if (this.samples.length > 4) this.samples.shift();
  }

  clear(): void {
    this.samples = [];
  }

  /** Positions at time t into out; false when nothing usable is loaded yet. */
  positions(t: number, out: Float64Array): boolean {
    const s = this.samples;
    if (!s.length) return false;
    for (let i = 0; i + 1 < s.length; i++) {
      if (t >= s[i].time && t <= s[i + 1].time) {
        interpolate(s[i], s[i + 1], t, out);
        return true;
      }
    }
    // Outside the samples: step from the nearest one along its velocity.
    const nearest = s.reduce((best, x) => (Math.abs(x.time - t) < Math.abs(best.time - t) ? x : best));
    if (nearest.time === t) {
      interpolate(nearest, nearest, t, out);
    } else {
      extrapolate(nearest, t, out);
    }
    return true;
  }

  /** The time to sample next, or null if the loaded samples already cover what is needed. */
  next(t: number, lead: number): number | null {
    const s = this.samples;
    if (!s.length) return t;
    const first = s[0].time;
    const last = s[s.length - 1].time;
    // Far outside what is loaded (a seek, or a long pause at another time): start over at t.
    if (t < first - lead || t > last + 4 * lead) return t;
    // Paused or slow: an exact sample at t is enough.
    if (lead <= 0) return s.some((x) => x.time === t) ? null : t;
    return last - t < lead ? Math.max(last, t) + lead : null;
  }

  get objects(): number {
    return this.count;
  }
}
