// The Python the browser simulator runs: the same files the API runs, imported as
// text, and the start-up code that builds the catalog. Shared by sim.worker.ts and
// the simulator's parity test (sim-python.test.ts).
import elements from "../../src/orbits/elements.py?raw";
import frames from "../../src/orbits/frames.py?raw";
import kepler from "../../src/orbits/kepler.py?raw";
import shells from "../../src/risk/shells.py?raw";
import breakup from "../../src/sim/breakup.py?raw";
import service from "../../src/sim/service.py?raw";

export const SOURCES: Record<string, string> = {
  "src/__init__.py": "",
  "src/orbits/__init__.py": "",
  "src/orbits/elements.py": elements,
  "src/orbits/frames.py": frames,
  "src/orbits/kepler.py": kepler,
  "src/risk/__init__.py": "",
  "src/risk/shells.py": shells,
  "src/sim/__init__.py": "",
  "src/sim/breakup.py": breakup,
  "src/sim/service.py": service,
};

/** Run after the sources are written to /sim: imports the simulator and builds `catalog` from `catalog_json`. */
export const BOOT = `
import json, sys
sys.path.insert(0, "/sim")
import pandas as pd
from src.sim import service
_c = json.loads(catalog_json)
catalog = service.Catalog(pd.DataFrame(_c["columns"]), pd.Timestamp(_c["snapshot_time"]))
del catalog_json, _c
`;

/** Run with `request_json` set: one simulation as JSON ({"ok": ...} or {"invalid": message}). */
export const RUN = `
try:
    _r = json.dumps({"ok": service.run(json.loads(request_json), catalog)})
except service.SimulationInputError as _e:
    _r = json.dumps({"invalid": str(_e)})
_r
`;
