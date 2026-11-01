import { describe, expect, it } from "vitest";
import { WINDOW_MS, currentTime, goLive, initialClock, offsetLabel, reachedEnd, seek, setSpeed, status, togglePlay } from "./clock";

const SNAP = Date.UTC(2026, 9, 31, 14, 49);
const H = 3_600_000;

describe("clock", () => {
  it("starts live at the real time when the data covers it", () => {
    const now = SNAP + 20 * H;
    const c = initialClock(SNAP, now);
    expect(c.mode).toBe("live");
    expect(currentTime(c, now)).toBe(now);
    expect(currentTime(c, now + 5_000)).toBe(now + 5_000); // follows the wall clock
    expect(status(c, now).kind).toBe("live");
  });

  it("parks at the end of the window when the data is too old", () => {
    const now = SNAP + WINDOW_MS + 10 * H;
    const c = initialClock(SNAP, now);
    expect(c.playing).toBe(false);
    expect(currentTime(c, now)).toBe(SNAP + WINDOW_MS);
    expect(status(c, now).kind).toBe("stale");
    expect(goLive(c, now).mode).toBe("forecast"); // there is no live to go back to
  });

  it("starts paused (but at the real time) with reduced motion", () => {
    const now = SNAP + H;
    const c = initialClock(SNAP, now, true);
    expect(c.playing).toBe(false);
    expect(currentTime(c, now + 10_000)).toBe(now);
  });

  it("forecast runs at its speed from the anchor and stops at the end", () => {
    const now = SNAP + H;
    const c = setSpeed(initialClock(SNAP, now), 60, now);
    expect(c.mode).toBe("forecast");
    expect(currentTime(c, now + 1_000)).toBe(now + 60_000);
    const late = now + WINDOW_MS; // far past the end at 60x
    expect(currentTime(c, late)).toBe(c.end);
    expect(reachedEnd(c, late)).toBe(true);
  });

  it("seeking is a paused forecast; back to live follows the real time again", () => {
    const now = SNAP + H;
    const tca = SNAP + 30 * H;
    const c = seek(initialClock(SNAP, now), tca, now);
    expect(currentTime(c, now + 60_000)).toBe(tca);
    const s = status(c, now);
    expect(s.kind).toBe("forecast");
    expect(offsetLabel(s.offsetMs)).toBe("+29 h");
    const live = goLive(c, now + 60_000);
    expect(live.mode).toBe("live");
    expect(currentTime(live, now + 120_000)).toBe(now + 120_000);
  });

  it("seek clamps to the window", () => {
    const now = SNAP + H;
    expect(currentTime(seek(initialClock(SNAP, now), SNAP - 10 * H, now), now)).toBe(SNAP);
  });

  it("pause keeps the time; play resumes from it; play at the end replays", () => {
    const now = SNAP + H;
    const paused = togglePlay(initialClock(SNAP, now), now);
    expect(paused.mode).toBe("forecast");
    expect(currentTime(paused, now + 10_000)).toBe(now);
    const atEnd = seek(paused, SNAP + WINDOW_MS, now);
    expect(currentTime(togglePlay(atEnd, now), now)).toBe(SNAP);
  });

  it("choosing real speed at the real time is live", () => {
    const now = SNAP + H;
    const c = setSpeed(setSpeed(initialClock(SNAP, now), 60, now), 1, now);
    expect(c.mode).toBe("live");
  });

  it("labels offsets readably", () => {
    expect(offsetLabel(20 * 60_000)).toBe("+20 min");
    expect(offsetLabel(-3 * H)).toBe("−3.0 h");
    expect(offsetLabel(60 * H)).toBe("+2.5 days");
  });
});
