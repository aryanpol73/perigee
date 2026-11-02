// The installed app's layout (src/pwa.ts decides when): the globe fills the screen
// and stays mounted under every other screen (paused while covered), a bottom
// navigation bar switches screens, and each screen keeps its state and scroll
// position once opened. The dashboard (src/App.tsx) is unchanged; both use the same
// data, store and views.
import { lazy, Suspense, useEffect, useRef, useState, type ReactNode } from "react";
import { fetchHealth, get, query, useApi, type ConjunctionPage, type Health, type Meta } from "../api";
import { setState, time, wallNow } from "../store";
import { ApproachesScreen } from "./ApproachesScreen";
import { GlobeOverlay } from "./GlobeScreen";
import { Icon, type IconName } from "./icons";
import { navigate, startHistory, useRoute, type Tab } from "./nav";
import { checkOnOpen } from "./notify";
import { RiskScreen } from "./RiskScreen";
import { SearchScreen } from "./SearchScreen";
import { SettingsScreen } from "./SettingsScreen";
import { checkForNewData, useToast } from "./toast";
import "./mobile.css";

const Globe = lazy(() => import("../globe/Globe"));
startHistory();

const NAV: [Tab, string, IconName][] = [
  ["globe", "Globe", "globe"],
  ["conjunctions", "Approaches", "approaches"],
  ["object", "Search", "search"],
  ["risk", "Risk", "risk"],
  ["settings", "Settings", "settings"],
];

export default function MobileApp() {
  const route = useRoute();
  const meta = useApi<Meta>("/v1/meta");
  const [health, setHealth] = useState<Health | null>(null);
  const [visited, setVisited] = useState<Set<Tab>>(() => new Set([route.tab]));
  const [toast, dismissToast] = useToast();
  const [splash, setSplash] = useState(true);
  const tab = route.tab;
  // Each screen keeps its last sub-view while it fades out (and while hidden).
  const subs = useRef<Partial<Record<Tab, string | null>>>({});
  subs.current[tab] = route.sub;
  const sub = (t: Tab) => subs.current[t] ?? null;

  if (!visited.has(tab)) setVisited(new Set([...visited, tab]));

  useEffect(() => {
    setState({ globePaused: tab !== "globe" });
  }, [tab]);

  // #/object/<norad> (a notification, a link) opens that object.
  useEffect(() => {
    if (tab === "object" && route.sub && /^\d+$/.test(route.sub)) setState({ selected: Number(route.sub), focus: null });
  }, [tab, route.sub]);

  useEffect(() => {
    fetchHealth().then(setHealth, () => setHealth(null));
  }, []);

  useEffect(() => {
    const m = meta.data;
    if (!m) return;
    time.init(new Date(m.snapshot_time).getTime());
    const t = window.setTimeout(() => setSplash(false), 350);
    // Objects in approaches above the operating point, for the "risk" colour mode.
    if (m.operating_probability !== null && m.operating_probability !== undefined) {
      get<ConjunctionPage>(`/v1/conjunctions${query({ min_probability: m.operating_probability, limit: 500 })}`).then(
        (p) => setState({ riskNorads: new Set(p.items.flatMap((c) => c.objects.map((o) => o.norad_id))) }),
        () => undefined,
      );
    }
    void checkOnOpen(m, wallNow()).catch(() => undefined);
    return () => window.clearTimeout(t);
  }, [meta.data]);

  // Back in the app after a while: has a newer snapshot been published?
  useEffect(() => {
    let hiddenAt = 0;
    const onVisible = () => {
      if (document.visibilityState === "hidden") hiddenAt = Date.now();
      else if (hiddenAt && Date.now() - hiddenAt > 10 * 60_000) void checkForNewData(true);
    };
    document.addEventListener("visibilitychange", onVisible);
    return () => document.removeEventListener("visibilitychange", onVisible);
  }, []);

  const m = meta.data;
  const screen = (t: Tab, body: ReactNode) => (
    <section key={t} className={`m-screen${tab === t ? " active" : ""}`} aria-hidden={tab !== t} inert={tab !== t} data-screen={t}>
      {visited.has(t) && body}
    </section>
  );

  return (
    <div className="m-app">
      <div className="m-globe-layer">
        <Suspense fallback={null}>{m && <Globe meta={m} variant="app" />}</Suspense>
        {m && <GlobeOverlay snapshot={m.snapshot_time} provisional={!m.history_complete} active={tab === "globe"} />}
      </div>

      {screen("conjunctions", <ApproachesScreen meta={m} openId={tab === "conjunctions" ? route.sub : null} />)}
      {screen("object", <SearchScreen active={tab === "object"} />)}
      {screen("risk", <RiskScreen simulator={sub("risk") === "simulator"} />)}
      {screen("settings", <SettingsScreen meta={m} health={health} method={tab === "settings" && route.sub === "method"} active={tab === "settings"} />)}

      {health?.status === "stale" && tab === "globe" && (
        <div className="m-alert" role="alert">
          Data is {health.data_age_hours.toFixed(0)} h old: the daily refresh may have failed.
        </div>
      )}

      {toast && (
        <div className="m-toast" role="status" key={toast.id}>
          <span>{toast.message}</span>
          {toast.action && (
            <button
              type="button"
              onClick={() => {
                toast.action!.run();
                dismissToast();
              }}
            >
              {toast.action.label}
            </button>
          )}
        </div>
      )}

      <nav className="m-nav" aria-label="Screens">
        {NAV.map(([t, label, icon]) => (
          <button key={t} type="button" aria-current={tab === t ? "page" : undefined} onClick={() => void navigate(t)}>
            <span className="m-nav-icon">
              <Icon name={icon} size={24} />
            </span>
            <span>{label}</span>
          </button>
        ))}
      </nav>

      {splash && (
        <div className={`m-splash${m ? " leaving" : ""}`} aria-hidden={Boolean(m)}>
          <img src="/icons/icon-192.png" alt="" width={112} height={112} />
          <strong>Perigee</strong>
          {meta.error ? (
            <div className="m-splash-error" role="alert">
              <p>Could not load the data. Check your connection.</p>
              <button type="button" className="m-button primary" onClick={() => window.location.reload()}>
                Try again
              </button>
            </div>
          ) : (
            <span className="m-spinner" role="status" aria-label="Loading" />
          )}
        </div>
      )}
    </div>
  );
}
