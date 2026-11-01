import { describe, expect, it } from "vitest";
import { ageDays, hoursAhead, km, objectTypeName, percent, riskLabel, utc } from "./format";
import { query } from "./api";

describe("formatting", () => {
  it("formats times in UTC", () => {
    expect(utc("2026-10-31T14:49:46Z")).toBe("2026-10-31 14:49 UTC");
  });

  it("formats estimates without false precision", () => {
    expect(percent(0.4267)).toBe("43%");
    expect(percent(0.004)).toBe("<1%");
    expect(percent(null)).toBe("no estimate");
    expect(km(0.71234)).toBe("0.71 km");
  });

  it("never says collide", () => {
    for (const p of [null, 0.01, 0.5, 0.99]) {
      expect(riskLabel(p, 0.2).toLowerCase()).not.toContain("collid");
    }
    expect(riskLabel(0.5, 0.2)).toBe("Elevated risk estimate");
    expect(riskLabel(0.1, 0.2)).toBe("Lower risk estimate");
    expect(riskLabel(null, 0.2)).toBe("Outside model scope");
  });

  it("describes ages and lead times", () => {
    expect(ageDays(0.5)).toBe("12 h old");
    expect(ageDays(3.25)).toBe("3.3 days old");
    expect(hoursAhead(0.25)).toBe("in 15 min");
    expect(hoursAhead(30)).toBe("in 30 h");
    expect(hoursAhead(72)).toBe("in 3.0 days");
    expect(objectTypeName("R/B")).toBe("Rocket body");
    expect(objectTypeName(undefined)).toBe("Unknown");
  });

  it("builds query strings, skipping empty values", () => {
    expect(query({ sort: "tca", limit: 25, max_miss_km: undefined, scored_only: "" })).toBe("?sort=tca&limit=25");
    expect(query({})).toBe("");
  });
});
