import { describe, expect, it } from "vitest";
import { ERROR_KM, errorLabel, forecastErrorKm, orbitClass, type OrbitClass } from "./uncertainty";

const DAY = 86_400_000;

describe("forecast uncertainty", () => {
  for (const cls of Object.keys(ERROR_KM) as OrbitClass[]) {
    it(`${cls}: grows with lead time and matches the measured table at its points`, () => {
      const table = ERROR_KM[cls];
      let prev = 0;
      for (const [d, median, p90] of table) {
        expect(forecastErrorKm(d * DAY, cls).median).toBeCloseTo(median, 6);
        expect(forecastErrorKm(d * DAY, cls).p90).toBeCloseTo(p90, 6);
        expect(median).toBeGreaterThanOrEqual(prev);
        expect(p90).toBeGreaterThan(median);
        prev = median;
      }
      expect(forecastErrorKm(0, cls).median).toBe(table[0][1]);
      expect(forecastErrorKm(30 * DAY, cls).median).toBe(table[table.length - 1][1]);
    });
  }

  it("sorts objects into the measured classes", () => {
    expect(orbitClass("STARLINK-31045", 480)).toBe("starlink");
    expect(orbitClass("ISS (ZARYA)", 415)).toBe("leo");
    expect(orbitClass("GPS BIIF-2", 20_000)).toBe("high");
    // Starlink drifts much faster than other low-orbit objects at every lead.
    for (const d of [1, 3, 7]) expect(forecastErrorKm(d * DAY, "starlink").median).toBeGreaterThan(5 * forecastErrorKm(d * DAY, "leo").median);
  });

  it("rounds so it never looks precise", () => {
    expect(errorLabel(0.3)).toBe("±~1 km");
    expect(errorLabel(3.6)).toBe("±~4 km");
    expect(errorLabel(17)).toBe("±~15 km");
    expect(errorLabel(123)).toBe("±~120 km");
    expect(errorLabel(1725)).toBe("±~1,700 km");
  });
});
