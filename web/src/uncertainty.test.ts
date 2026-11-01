import { describe, expect, it } from "vitest";
import { LEO_ERROR_KM, errorLabel, forecastErrorKm } from "./uncertainty";

const DAY = 86_400_000;

describe("forecast uncertainty", () => {
  it("grows with lead time and matches the measured table at its points", () => {
    let prev = 0;
    for (const [d, median, p90] of LEO_ERROR_KM) {
      expect(forecastErrorKm(d * DAY).median).toBeCloseTo(median, 6);
      expect(forecastErrorKm(d * DAY).p90).toBeCloseTo(p90, 6);
      expect(median).toBeGreaterThanOrEqual(prev);
      expect(p90).toBeGreaterThan(median);
      prev = median;
    }
    expect(forecastErrorKm(0).median).toBe(LEO_ERROR_KM[0][1]);
    expect(forecastErrorKm(30 * DAY).median).toBe(LEO_ERROR_KM[LEO_ERROR_KM.length - 1][1]);
  });

  it("rounds so it never looks precise", () => {
    expect(errorLabel(0.3)).toBe("±~1 km");
    expect(errorLabel(3.6)).toBe("±~4 km");
    expect(errorLabel(17)).toBe("±~15 km");
    expect(errorLabel(123)).toBe("±~120 km");
  });
});
