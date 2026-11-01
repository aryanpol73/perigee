// The globe's clock. Two modes:
//   live      follows the real time at real speed: positions are where the objects
//             are now (as computed from the latest public orbit data);
//   forecast  any time in the data's window, at any speed: what the orbit data
//             predicts. Selecting a close approach, dragging the slider or
//             speeding up switches to it; "Back to live" returns.
// Time is stored as an anchor (a simulated time and the real moment it was set),
// so nothing has to be written 60 times a second; the current time is computed.

export const WINDOW_MS = 7 * 86_400_000; // the screening covers 7 days after the snapshot

export type Mode = "live" | "forecast";

export interface Clock {
  mode: Mode;
  playing: boolean;
  speed: number; // simulated seconds per real second (forecast)
  anchorSim: number; // simulated ms since the epoch at anchorWall
  anchorWall: number; // real ms since the epoch when anchored
  start: number; // window start (snapshot time)
  end: number; // window end
}

export type Status =
  | { kind: "live"; offsetMs: 0 }
  | { kind: "forecast"; offsetMs: number } // simulated minus real time
  | { kind: "stale"; offsetMs: number }; // the real time is past the data's window

const clamp = (t: number, c: Clock) => Math.min(c.end, Math.max(c.start, t));

/** Live at the real time if the window covers it; otherwise park at the edge. */
export function initialClock(snapshotMs: number, wallNow: number, reducedMotion = false): Clock {
  const base: Clock = {
    mode: "live",
    playing: !reducedMotion,
    speed: 1,
    anchorSim: wallNow,
    anchorWall: wallNow,
    start: snapshotMs,
    end: snapshotMs + WINDOW_MS,
  };
  if (wallNow > base.end || wallNow < base.start) {
    return { ...base, mode: "forecast", playing: false, anchorSim: clamp(wallNow, base) };
  }
  return reducedMotion ? { ...base, mode: "forecast", playing: false } : base;
}

/** The simulated time now. */
export function currentTime(c: Clock, wallNow: number): number {
  if (c.mode === "live") return clamp(wallNow, c);
  const elapsed = c.playing ? (wallNow - c.anchorWall) * c.speed : 0;
  return clamp(c.anchorSim + elapsed, c);
}

export function status(c: Clock, wallNow: number): Status {
  if (wallNow > c.end) return { kind: "stale", offsetMs: currentTime(c, wallNow) - wallNow };
  if (c.mode === "live") return { kind: "live", offsetMs: 0 };
  return { kind: "forecast", offsetMs: currentTime(c, wallNow) - wallNow };
}

/** True when a playing forecast has run into the end of the window (it should stop). */
export function reachedEnd(c: Clock, wallNow: number): boolean {
  return c.mode === "forecast" && c.playing && currentTime(c, wallNow) >= c.end;
}

// --- Transitions (each returns a new clock) -------------------------------------

export function goLive(c: Clock, wallNow: number): Clock {
  if (wallNow > c.end || wallNow < c.start) return { ...c, mode: "forecast", playing: false, anchorSim: clamp(wallNow, c), anchorWall: wallNow };
  return { ...c, mode: "live", playing: true, speed: 1, anchorSim: wallNow, anchorWall: wallNow };
}

export function seek(c: Clock, time: number, wallNow: number, playing = false): Clock {
  return { ...c, mode: "forecast", playing, anchorSim: clamp(time, c), anchorWall: wallNow };
}

export function setSpeed(c: Clock, speed: number, wallNow: number): Clock {
  const t = currentTime(c, wallNow);
  // Real speed at the real time is simply live.
  if (speed === 1 && Math.abs(t - wallNow) < 1_000 && wallNow <= c.end && wallNow >= c.start) return goLive(c, wallNow);
  return { ...c, mode: "forecast", playing: true, speed, anchorSim: t, anchorWall: wallNow };
}

export function togglePlay(c: Clock, wallNow: number): Clock {
  const t = currentTime(c, wallNow);
  if (c.playing) return { ...c, mode: "forecast", playing: false, anchorSim: t, anchorWall: wallNow };
  if (t >= c.end) return { ...c, mode: "forecast", playing: true, anchorSim: c.start, anchorWall: wallNow }; // replay
  return { ...c, playing: true, anchorSim: t, anchorWall: wallNow };
}

/** "+29 h", "-3 h", "+2.5 days" */
export function offsetLabel(ms: number): string {
  const h = ms / 3_600_000;
  const sign = h >= 0 ? "+" : "−";
  const a = Math.abs(h);
  if (a < 1) return `${sign}${Math.round(a * 60)} min`;
  if (a < 48) return `${sign}${a.toFixed(a < 10 ? 1 : 0)} h`;
  return `${sign}${(a / 24).toFixed(1)} days`;
}
