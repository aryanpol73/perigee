// Propagate OMM records with satellite.js, the library the browser globe uses.
// stdin:  {"records": [OMM objects], "times": [ISO 8601 UTC strings]}
// stdout: {"results": [{"NORAD_CAT_ID", "eci": [[x,y,z]|null], "geodetic": [[lat,lon,h]|null], "error"}]}
import { readFileSync } from "node:fs";
import * as satellite from "satellite.js";

const { records, times } = JSON.parse(readFileSync(0, "utf8"));
const dates = times.map((t) => new Date(t));
const gmst = dates.map((d) => satellite.gstime(d));

const results = records.map((omm) => {
  const satrec = satellite.json2satrec(omm);
  const eci = [];
  const geodetic = [];
  dates.forEach((date, i) => {
    const state = satellite.propagate(satrec, date);
    if (state === null) {
      eci.push(null);
      geodetic.push(null);
      return;
    }
    const p = state.position;
    const gd = satellite.eciToGeodetic(p, gmst[i]);
    eci.push([p.x, p.y, p.z]);
    geodetic.push([
      satellite.degreesLat(gd.latitude),
      satellite.degreesLong(gd.longitude),
      gd.height,
    ]);
  });
  return { NORAD_CAT_ID: omm.NORAD_CAT_ID, eci, geodetic, error: satrec.error ?? 0 };
});

process.stdout.write(JSON.stringify({ results }));
