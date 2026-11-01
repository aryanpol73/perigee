import { lazy, Suspense, useEffect, useState } from "react";
import { apiUrl, get, useApi, type Health, type Meta } from "./api";
import { ConjunctionsView } from "./views/ConjunctionsView";
import { MethodView } from "./views/MethodView";
import { ObjectView } from "./views/ObjectView";
import { RiskView } from "./views/RiskView";
import { SimulatorView } from "./views/SimulatorView";
import { utc } from "./format";
import { time, useStore } from "./store";

// Cesium is large: load it after the shell of the page is on screen.
const Globe = lazy(() => import("./globe/Globe"));

const TABS = [
  ["conjunctions", "Close approaches"],
  ["object", "Object"],
  ["risk", "Risk by altitude"],
  ["simulator", "Simulator"],
  ["method", "Method and limits"],
] as const;
type Tab = (typeof TABS)[number][0];

function currentTab(): Tab {
  const t = window.location.hash.replace(/^#\/?/, "").split("/")[0];
  return (TABS.map((x) => x[0]) as string[]).includes(t) ? (t as Tab) : "conjunctions";
}

export function go(tab: Tab): void {
  window.location.hash = `/${tab}`;
}

export default function App() {
  const [tab, setTab] = useState<Tab>(currentTab());
  const [slow, setSlow] = useState(false);
  const meta = useApi<Meta>("/v1/meta");
  const [health, setHealth] = useState<Health | null>(null);
  const selected = useStore((s) => s.selected);

  useEffect(() => {
    const onHash = () => setTab(currentTab());
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);

  useEffect(() => {
    // Free hosts sleep; say so if the first answer is slow.
    const timer = setTimeout(() => setSlow(true), 3000);
    fetch(apiUrl("/v1/health"))
      .then((r) => r.json())
      .then((h: Health) => setHealth(h))
      .catch(() => setHealth(null))
      .finally(() => {
        clearTimeout(timer);
        setSlow(false);
      });
    return () => clearTimeout(timer);
  }, []);

  useEffect(() => {
    if (meta.data) time.init(new Date(meta.data.snapshot_time).getTime());
  }, [meta.data]);

  useEffect(() => {
    if (selected !== null && tab !== "object" && tab !== "simulator") go("object");
    // Only a new selection opens the object tab; changing tabs afterwards must not.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [selected]);

  useEffect(() => {
    void get("/v1/model").catch(() => undefined); // warm the cache for the method page
  }, []);

  return (
    <div className="app">
      <header className="topbar">
        <h1>
          Perigee <span className="subtitle">orbital debris risk from public data</span>
        </h1>
        <nav aria-label="Views">
          {TABS.map(([key, label]) => (
            <a key={key} href={`#/${key}`} aria-current={tab === key ? "page" : undefined}>
              {label}
            </a>
          ))}
        </nav>
      </header>

      {slow && <div className="banner" role="status">Waking the server: the first load can take up to a minute on the free host.</div>}
      {meta.error && <div className="banner warn" role="alert">The API is not answering ({meta.error.message}). Showing nothing rather than old data.</div>}
      {health?.status === "stale" && (
        <div className="banner warn" role="alert">
          Data is {health.data_age_hours.toFixed(0)} hours old (limit {health.max_data_age_hours} h); the refresh job may have failed.
        </div>
      )}
      {meta.data && !meta.data.history_complete && meta.data.model_version && (
        <div className="banner" role="status">
          Provisional risk estimates: this run had under 14 days of update history ({meta.data.history_days_for_features ?? 0} days).
          In testing, estimates made without history ran at about half the observed rate and ranked approaches less well.
        </div>
      )}

      <main className="layout">
        <section className="globe-area" aria-label="Globe">
          <Suspense fallback={<div className="globe-loading">Loading the globe…</div>}>{meta.data && <Globe meta={meta.data} />}</Suspense>
        </section>
        <aside className="panel" aria-label="Details">
          {tab === "conjunctions" && <ConjunctionsView meta={meta.data} />}
          {tab === "object" && <ObjectView />}
          {tab === "risk" && <RiskView />}
          {tab === "simulator" && <SimulatorView />}
          {tab === "method" && <MethodView meta={meta.data} />}
        </aside>
      </main>

      <footer className="footer">
        {meta.data ? (
          <>
            <span>Snapshot {utc(meta.data.snapshot_time)}</span>
            <span>Model {meta.data.model_version ?? "unavailable"}</span>
            <span>{meta.data.object_count.toLocaleString()} objects</span>
          </>
        ) : (
          <span>Loading…</span>
        )}
        <strong>Estimates from public data; not for operational use.</strong>
        <span className="muted">Data: CelesTrak, Space-Track.org (USSPACECOM). Earth imagery: NASA Blue Marble &amp; Black Marble (GIBS).</span>
      </footer>
    </div>
  );
}
