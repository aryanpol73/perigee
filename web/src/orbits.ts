// The orbit bulk file and what the globe does with it: decode, filter, colour.

export const ORBIT_FIELDS = [
  "NORAD_CAT_ID", "OBJECT_NAME", "OBJECT_TYPE", "EPOCH", "MEAN_MOTION", "ECCENTRICITY", "INCLINATION",
  "RA_OF_ASC_NODE", "ARG_OF_PERICENTER", "MEAN_ANOMALY", "BSTAR", "MEAN_MOTION_DOT", "MEAN_MOTION_DDOT",
  "EPHEMERIS_TYPE", "CLASSIFICATION_TYPE", "ELEMENT_SET_NO", "REV_AT_EPOCH",
] as const;

export interface OrbitFile {
  fields: string[];
  rows: (string | number)[][];
}

export interface Omm {
  NORAD_CAT_ID: number;
  OBJECT_NAME: string;
  OBJECT_TYPE: string;
  EPOCH: string;
  MEAN_MOTION: number;
  ECCENTRICITY: number;
  INCLINATION: number;
  RA_OF_ASC_NODE: number;
  ARG_OF_PERICENTER: number;
  MEAN_ANOMALY: number;
  BSTAR: number;
  MEAN_MOTION_DOT: number;
  MEAN_MOTION_DDOT: number;
  EPHEMERIS_TYPE: number;
  CLASSIFICATION_TYPE: string;
  ELEMENT_SET_NO: number;
  REV_AT_EPOCH: number;
}

export function decodeOrbitFile(file: OrbitFile): Omm[] {
  const index = ORBIT_FIELDS.map((f) => {
    const i = file.fields.indexOf(f);
    if (i < 0) throw new Error(`orbit file is missing ${f}`);
    return i;
  });
  return file.rows.map((row) => {
    const o: Record<string, string | number> = {};
    ORBIT_FIELDS.forEach((f, k) => (o[f] = row[index[k]]));
    return o as unknown as Omm;
  });
}

const MU = 398600.4418;
const EARTH_RADIUS_KM = 6378.137;

/** Mean perigee and apogee altitude (km) from mean motion and eccentricity. */
export function altitudes(o: Pick<Omm, "MEAN_MOTION" | "ECCENTRICITY">): { perigee: number; apogee: number } {
  const n = (o.MEAN_MOTION * 2 * Math.PI) / 86400;
  const a = Math.cbrt(MU / (n * n));
  return { perigee: a * (1 - o.ECCENTRICITY) - EARTH_RADIUS_KM, apogee: a * (1 + o.ECCENTRICITY) - EARTH_RADIUS_KM };
}

export type ObjectType = "PAY" | "DEB" | "R/B" | "UNK";
export const OBJECT_TYPES: ObjectType[] = ["PAY", "DEB", "R/B", "UNK"];

export interface Filters {
  types: Record<ObjectType, boolean>;
  minAltKm: number;
  maxAltKm: number;
}

export const DEFAULT_FILTERS: Filters = {
  types: { PAY: true, DEB: true, "R/B": true, UNK: true },
  minAltKm: 0,
  maxAltKm: 50000,
};

/** On small screens start with payloads only (the user can turn the rest on). */
export const PHONE_FILTERS: Filters = { ...DEFAULT_FILTERS, types: { PAY: true, DEB: false, "R/B": false, UNK: false } };

export function visible(o: Omm, f: Filters): boolean {
  const t = (OBJECT_TYPES as string[]).includes(o.OBJECT_TYPE) ? (o.OBJECT_TYPE as ObjectType) : "UNK";
  if (!f.types[t]) return false;
  const { perigee, apogee } = altitudes(o);
  return apogee >= f.minAltKm && perigee <= f.maxAltKm;
}

// Object colours on the dark globe: the categorical blue / orange / green of the
// validated palette, lifted in lightness so small points read against space.
export const TYPE_COLORS: Record<ObjectType, string> = {
  PAY: "#6fb2ff",
  DEB: "#ff8a4c",
  "R/B": "#3fd99a",
  UNK: "#c9c6bd",
};

/** Crewed stations get their own colour and a larger point. */
export const STATION_COLOR = "#a8f7ff";
export const STATIONS = new Set([25544, 48274]); // ISS (ZARYA), CSS (TIANHE)

export type ColorMode = "type" | "altitude" | "risk";

/** One blue hue, light (low orbit) to deeper blue (high orbit), for altitude. */
export function altitudeColor(perigeeKm: number): string {
  const t = Math.min(1, Math.max(0, Math.log10(Math.max(perigeeKm, 200) / 200) / Math.log10(36000 / 200)));
  const light = [200, 232, 255];
  const dark = [64, 104, 214]; // stays visible against space
  const c = light.map((l, i) => Math.round(l + (dark[i] - l) * t));
  return `rgb(${c[0]},${c[1]},${c[2]})`;
}
