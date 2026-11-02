import { describe, expect, it } from "vitest";
import type { ObjectDetail } from "../api";
import { isAppMode } from "../pwa";
import { parseRoute } from "./nav";
import { ALERT_HOURS, watchAlerts } from "./notify";

describe("parseRoute", () => {
  it("maps the dashboard's hashes onto the app's screens", () => {
    expect(parseRoute("")).toEqual({ tab: "globe", sub: null });
    expect(parseRoute("#/globe")).toEqual({ tab: "globe", sub: null });
    expect(parseRoute("#/conjunctions")).toEqual({ tab: "conjunctions", sub: null });
    expect(parseRoute("#/conjunctions/20261031_1_2_3")).toEqual({ tab: "conjunctions", sub: "20261031_1_2_3" });
    expect(parseRoute("#/object/25544")).toEqual({ tab: "object", sub: "25544" });
    expect(parseRoute("#/simulator")).toEqual({ tab: "risk", sub: "simulator" });
    expect(parseRoute("#/method")).toEqual({ tab: "settings", sub: "method" });
    expect(parseRoute("#/nonsense")).toEqual({ tab: "globe", sub: null });
  });
});

function fakeWindow({ search = "", standalone = false, coarse = true, narrow = true, referrer = "" } = {}): Window {
  const media: Record<string, boolean> = {
    "(display-mode: standalone)": standalone,
    "(display-mode: fullscreen)": false,
    "(pointer: coarse)": coarse,
    "(max-width: 900px)": narrow,
  };
  return {
    location: { search },
    matchMedia: (q: string) => ({ matches: media[q] ?? false }),
    navigator: {},
    document: { referrer },
  } as unknown as Window;
}

describe("isAppMode", () => {
  it("is the installed app on a phone only", () => {
    expect(isAppMode(fakeWindow())).toBe(false); // a phone's browser: the website
    expect(isAppMode(fakeWindow({ standalone: true }))).toBe(true);
    expect(isAppMode(fakeWindow({ referrer: "android-app://io.github.aryanpol73.perigee/" }))).toBe(true); // the APK
    expect(isAppMode(fakeWindow({ standalone: true, coarse: false, narrow: false }))).toBe(false); // installed on a desktop
  });

  it("can be forced either way", () => {
    expect(isAppMode(fakeWindow({ search: "?app=1", coarse: false, narrow: false }))).toBe(true);
    expect(isAppMode(fakeWindow({ search: "?app=0", standalone: true }))).toBe(false);
  });
});

describe("watchAlerts", () => {
  const now = Date.parse("2026-11-01T12:00:00Z");
  const at = (h: number) => new Date(now + h * 3_600_000).toISOString();
  const approach = (id: string, h: number, miss: number, p: number | null) => ({
    id,
    tca: at(h),
    miss_distance_km: miss,
    risk_probability_estimate: p,
    objects: [
      { norad_id: 25544, name: "ISS (ZARYA)" },
      { norad_id: 99, name: `DEB ${id}` },
    ],
  });
  const iss = (conjunctions: unknown[]) => ({ norad_id: 25544, name: "ISS (ZARYA)", conjunctions }) as unknown as ObjectDetail;

  it("tells about the soonest close or elevated approach within the window", () => {
    const alerts = watchAlerts(
      iss([
        approach("far", 30, 3.5, 0.05), // neither close nor elevated
        approach("late", ALERT_HOURS + 5, 0.2, 0.9), // beyond the window
        approach("past", -2, 0.1, 0.9),
        approach("elevated", 20, 2.0, 0.4),
        approach("close", 8, 0.6, null),
      ]),
      now,
      0.3,
    );
    expect(alerts).toHaveLength(1);
    expect(alerts[0].title).toBe("ISS (ZARYA): close approach in 8 h");
    expect(alerts[0].body).toContain("0.60 km from DEB close");
    expect(alerts[0].body).toContain("+1 more");
    expect(alerts[0].body).toContain("not for operational use");
  });

  it("says nothing when nothing qualifies", () => {
    expect(watchAlerts(iss([approach("far", 30, 3.5, 0.05)]), now, 0.3)).toEqual([]);
    expect(watchAlerts(iss([approach("unscored", 10, 2.5, 0.9)]), now, null)).toEqual([]); // no operating point: misses only
  });
});
