// Risk by altitude and the breakup simulator, the dashboard's own views in app
// styling. Both stay mounted once opened, so a running simulation keeps animating on
// the globe while another screen is shown.
import { useState } from "react";
import { useStore } from "../store";
import { RiskView } from "../views/RiskView";
import { SimulatorView } from "../views/SimulatorView";
import { Icon } from "./icons";
import { navigate } from "./nav";
import { PullToRefresh } from "./PullToRefresh";
import { checkForNewData } from "./toast";

export function RiskScreen({ simulator }: { simulator: boolean }) {
  const [opened, setOpened] = useState(simulator);
  const sim = useStore((s) => s.sim);
  if (simulator && !opened) setOpened(true);
  const show = (sub: "risk" | "simulator") => void navigate(sub);

  return (
    <PullToRefresh onRefresh={() => checkForNewData().then(() => undefined)} label="Risk">
      <header className="m-screen-head">
        <h1>{simulator ? "Breakup simulator" : "Risk by altitude"}</h1>
      </header>
      <div className="m-segmented wide" role="tablist" aria-label="Risk views">
        <button type="button" role="tab" aria-selected={!simulator} onClick={() => show("risk")}>
          <Icon name="risk" size={17} /> By altitude
        </button>
        <button type="button" role="tab" aria-selected={simulator} onClick={() => show("simulator")}>
          <Icon name="burst" size={17} /> Simulator
        </button>
      </div>
      {simulator && sim && (
        <button type="button" className="m-banner-button" onClick={() => void navigate("globe")}>
          <Icon name="globe" size={18} /> {sim.response.modelled_fragments.toLocaleString()} fragments simulated: see them on the globe
          <Icon name="chevron" size={16} />
        </button>
      )}
      <div className="m-card m-view" hidden={simulator}>
        <RiskView />
      </div>
      {opened && (
        <div className="m-card m-view" hidden={!simulator}>
          <SimulatorView />
        </div>
      )}
    </PullToRefresh>
  );
}
