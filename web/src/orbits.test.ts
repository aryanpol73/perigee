import { describe, expect, it } from "vitest";
import { json2satrec, propagate } from "satellite.js";
import reference from "./__fixtures__/sgp4_reference.json";
import { DEFAULT_FILTERS, PHONE_FILTERS, altitudeColor, altitudes, decodeOrbitFile, visible, type Omm } from "./orbits";

const iss: Omm = {
  NORAD_CAT_ID: 25544, OBJECT_NAME: "ISS (ZARYA)", OBJECT_TYPE: "PAY", EPOCH: "2026-10-31T06:00:00.000000",
  MEAN_MOTION: 15.5, ECCENTRICITY: 0.0005, INCLINATION: 51.6, RA_OF_ASC_NODE: 100, ARG_OF_PERICENTER: 50,
  MEAN_ANOMALY: 10, BSTAR: 0.0002, MEAN_MOTION_DOT: 0.0001, MEAN_MOTION_DDOT: 0, EPHEMERIS_TYPE: 0,
  CLASSIFICATION_TYPE: "U", ELEMENT_SET_NO: 999, REV_AT_EPOCH: 1,
};

describe("orbit file", () => {
  it("decodes rows by field name, whatever the column order", () => {
    const fields = Object.keys(iss).reverse();
    const decoded = decodeOrbitFile({ fields, rows: [fields.map((f) => iss[f as keyof Omm])] });
    expect(decoded[0]).toEqual(iss);
  });

  it("refuses a file missing a field", () => {
    expect(() => decodeOrbitFile({ fields: ["NORAD_CAT_ID"], rows: [[1]] })).toThrow(/missing/);
  });
});

describe("altitudes and filters", () => {
  it("puts the ISS near 400 km", () => {
    const { perigee, apogee } = altitudes(iss);
    expect(perigee).toBeGreaterThan(380);
    expect(apogee).toBeLessThan(440);
  });

  it("filters by type and altitude", () => {
    expect(visible(iss, DEFAULT_FILTERS)).toBe(true);
    expect(visible({ ...iss, OBJECT_TYPE: "DEB" }, PHONE_FILTERS)).toBe(false);
    expect(visible(iss, { ...DEFAULT_FILTERS, maxAltKm: 300 })).toBe(false);
    expect(visible({ ...iss, OBJECT_TYPE: "SOMETHING" }, { ...DEFAULT_FILTERS, types: { ...DEFAULT_FILTERS.types, UNK: false } })).toBe(false);
  });

  it("darkens with altitude", () => {
    const lum = (c: string) => c.match(/\d+/g)!.map(Number).reduce((a, b) => a + b, 0);
    expect(lum(altitudeColor(400))).toBeGreaterThan(lum(altitudeColor(20000)));
  });
});

describe("satellite.js agrees with Python SGP4", () => {
  // Python and the browser implement the same SGP4; satellite.js reads the epoch to
  // the millisecond, which at 7.7 km/s is a few metres. 50 m is the tolerance.
  const TOLERANCE_KM = 0.05;
  for (const c of reference.cases) {
    it(`${c.record.OBJECT_NAME} (${c.record.NORAD_CAT_ID})`, () => {
      const sat = json2satrec(c.record as never);
      for (const p of c.points) {
        const pv = propagate(sat, new Date(p.time));
        expect(pv).not.toBeNull();
        const pos = pv!.position as { x: number; y: number; z: number };
        const d = Math.hypot(pos.x - p.teme_km[0], pos.y - p.teme_km[1], pos.z - p.teme_km[2]);
        expect(d).toBeLessThan(TOLERANCE_KM);
      }
    });
  }
});
