// Find an object by name or NORAD id (results as you type), the watch list, and one
// object's page: its orbit, data age and upcoming close approaches.
import { useEffect, useState } from "react";
import { get, query, useApi, type Meta, type ObjectDetail, type Schemas } from "../api";
import { ageDays, hoursAhead, hoursFromNow, km, objectTypeName, utc } from "../format";
import { TYPE_COLORS, type ObjectType } from "../orbits";
import { moveCamera, setState, useStore, wallNow } from "../store";
import { Risk, focusApproach } from "../views/ConjunctionsView";
import { ShareButton } from "./ApproachesScreen";
import { WatchButton } from "./GlobeScreen";
import { Icon } from "./icons";
import { navigate, useBackClose } from "./nav";
import { useWatchList } from "./notify";
import { PullToRefresh } from "./PullToRefresh";
import { checkForNewData } from "./toast";

type ObjectPage = Schemas["ObjectPage"];
type Summary = Schemas["ObjectSummary"];

const SUGGESTIONS = ["ISS", "Tiangong", "Hubble", "Starlink", "Fengyun"];
const dot = (t: string | null) => TYPE_COLORS[t && t in TYPE_COLORS ? (t as ObjectType) : "UNK"];

export function SearchScreen({ active }: { active: boolean }) {
  const [q, setQ] = useState("");
  const [term, setTerm] = useState("");
  const [results, setResults] = useState<Summary[] | null>(null);
  const selected = useStore((s) => s.selected);
  const watch = useWatchList();

  // Results as you type, a moment after the last key.
  useEffect(() => {
    const t = window.setTimeout(() => setTerm(q.trim()), 220);
    return () => window.clearTimeout(t);
  }, [q]);
  useEffect(() => {
    if (term.length < 2 && !/^\d+$/.test(term)) {
      setResults(null);
      return;
    }
    let live = true;
    get<ObjectPage>(`/v1/objects${query({ q: term, limit: 25 })}`).then(
      (p) => live && setResults(p.items),
      () => live && setResults([]),
    );
    return () => {
      live = false;
    };
  }, [term]);

  const open = (norad: number) => setState({ selected: norad, focus: null });

  return (
    <>
      <PullToRefresh onRefresh={() => checkForNewData().then(() => undefined)} label="Search objects">
        <header className="m-screen-head">
          <h1>Search</h1>
          <p className="m-muted">Satellites, rocket bodies and debris in this snapshot.</p>
        </header>
        <form
          className="m-search"
          role="search"
          onSubmit={(e) => {
            e.preventDefault();
            setTerm(q.trim());
            (document.activeElement as HTMLElement | null)?.blur(); // closes the keyboard
          }}
        >
          <Icon name="search" size={20} />
          <input
            type="search"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            placeholder="Name or NORAD id, e.g. ISS or 25544"
            aria-label="Find by name or NORAD id"
            enterKeyHint="search"
            autoComplete="off"
          />
          {q && (
            <button type="button" className="m-icon-button" onClick={() => setQ("")} aria-label="Clear">
              <Icon name="close" size={18} />
            </button>
          )}
        </form>

        {results === null ? (
          <>
            <div className="m-chips wrap" aria-label="Suggestions">
              {SUGGESTIONS.map((s) => (
                <button key={s} type="button" className="m-chip" onClick={() => setQ(s)}>
                  {s}
                </button>
              ))}
            </div>
            <h2 className="m-section-title">
              <Icon name="starFilled" size={16} /> Watching
            </h2>
            {watch.length === 0 ? (
              <p className="m-muted m-pad">Tap Watch on an object to keep it here and get alerts about its close approaches.</p>
            ) : (
              <ul className="m-list">
                {watch.map((n) => (
                  <WatchedRow key={n} norad={n} onOpen={open} />
                ))}
              </ul>
            )}
          </>
        ) : results.length === 0 ? (
          <div className="m-empty">
            <p>No match for “{term}” in this snapshot.</p>
          </div>
        ) : (
          <ul className="m-list" aria-label="Results">
            {results.map((o) => (
              <li key={o.norad_id}>
                <button type="button" className="m-row m-row-button" onClick={() => open(o.norad_id)}>
                  <span className="m-type-dot" style={{ background: dot(o.object_type) }} aria-hidden />
                  <span className="m-row-text">
                    <strong>{o.name ?? o.norad_id}</strong>
                    <small>
                      {objectTypeName(o.object_type)} · {o.norad_id} · {o.perigee_km.toFixed(0)}–{o.apogee_km.toFixed(0)} km
                    </small>
                  </span>
                  <Icon name="chevron" size={18} className="m-chev" />
                </button>
              </li>
            ))}
          </ul>
        )}
      </PullToRefresh>
      <ObjectPage norad={selected} active={active} />
    </>
  );
}

function WatchedRow({ norad, onOpen }: { norad: number; onOpen: (n: number) => void }) {
  const d = useApi<ObjectDetail>(`/v1/objects/${norad}`);
  const o = d.data;
  const next = o?.conjunctions.filter((c) => new Date(c.tca).getTime() > wallNow()).sort((a, b) => a.tca.localeCompare(b.tca))[0];
  return (
    <li>
      <button type="button" className="m-row m-row-button" onClick={() => onOpen(norad)}>
        <span className="m-type-dot" style={{ background: dot(o?.object_type ?? "UNK") }} aria-hidden />
        <span className="m-row-text">
          <strong>{o?.name ?? norad}</strong>
          <small>
            {d.error ? "Not in this snapshot" : !o ? "Loading…" : next ? `Next approach ${hoursAhead(hoursFromNow(next.tca, wallNow()))}, ${km(next.miss_distance_km)}` : "No close approaches in 7 days"}
          </small>
        </span>
        <Icon name="chevron" size={18} className="m-chev" />
      </button>
    </li>
  );
}

/** One object, as a page that slides over the search screen. */
function ObjectPage({ norad, active }: { norad: number | null; active: boolean }) {
  const [shown, setShown] = useState<number | null>(norad);
  useEffect(() => {
    if (norad !== null) setShown(norad);
  }, [norad]);
  const close = () => setState({ selected: null });
  const back = useBackClose(active && norad !== null, close);
  const meta = useApi<Meta>("/v1/meta");
  const d = useApi<ObjectDetail>(shown !== null ? `/v1/objects/${shown}` : null);
  const o = d.data;
  const op = meta.data?.operating_probability ?? null;

  return (
    <div className={`m-page${norad !== null ? " open" : ""}`} aria-hidden={norad === null} inert={norad === null}>
      <header className="m-page-bar">
        <button type="button" className="m-icon-button" onClick={back} aria-label="Back to search">
          <Icon name="back" size={22} />
        </button>
        <strong>{o?.name ?? "Object"}</strong>
      </header>
      <div className="m-scroll">
        {d.loading && !o && <p className="m-muted m-pad">Loading…</p>}
        {d.error && <p className="m-pad">This object is not in the current snapshot.</p>}
        {o && (
          <div className="m-detail">
            <div className="m-hero">
              <span className="m-type-dot big" style={{ background: dot(o.object_type) }} aria-hidden />
              <div>
                <h1>{o.name}</h1>
                <p className="m-muted">
                  {objectTypeName(o.object_type)}
                  {o.rcs_size ? `, radar size ${o.rcs_size.toLowerCase()}` : ""} · NORAD {o.norad_id}
                </p>
              </div>
            </div>
            <div className="m-actions">
              <button
                type="button"
                className="m-button primary"
                onClick={() => void navigate("globe").then(() => moveCamera({ kind: "object", norad: o.norad_id }))}
              >
                <Icon name="globe" size={18} /> Show on globe
              </button>
              <WatchButton norad={o.norad_id} />
              <ShareButton title={o.name ?? `NORAD ${o.norad_id}`} text={`${o.name} (NORAD ${o.norad_id}) on Perigee: orbit and close approaches from public data.`} />
            </div>
            <div className="m-stats">
              <div><small>Perigee</small><strong>{o.perigee_km.toFixed(0)} km</strong></div>
              <div><small>Apogee</small><strong>{o.apogee_km.toFixed(0)} km</strong></div>
              <div><small>Inclination</small><strong>{o.inclination_deg.toFixed(1)}°</strong></div>
              <div><small>Period</small><strong>{o.period_min.toFixed(1)} min</strong></div>
            </div>
            <dl className="m-facts">
              <div><dt>Designator</dt><dd>{o.object_id ?? "unknown"}</dd></div>
              {o.owner && <div><dt>Owner</dt><dd>{o.owner}</dd></div>}
              {o.launch_date && <div><dt>Launched</dt><dd>{o.launch_date.slice(0, 10)}</dd></div>}
              <div><dt>Orbit data</dt><dd>epoch {utc(o.epoch)}, {ageDays(o.epoch_age_days)} at the snapshot</dd></div>
            </dl>
            <h2 className="m-section-title">Close approaches in the next 7 days ({o.conjunctions.length})</h2>
            {o.conjunctions.length === 0 && <p className="m-muted m-pad">None within 5 km with objects of other constellations.</p>}
            <ul className="m-cards">
              {o.conjunctions.slice(0, 50).map((c) => {
                const other = c.objects.find((x) => x.norad_id !== o.norad_id) ?? c.objects[1];
                return (
                  <li key={c.id}>
                    <button
                      type="button"
                      className="m-card m-approach small"
                      onClick={() => void navigate("globe").then(() => focusApproach(c))}
                    >
                      <span className="m-approach-names">
                        <span>
                          <span className="m-type-dot" style={{ background: dot(other.object_type) }} aria-hidden />
                          {other.name ?? other.norad_id}
                        </span>
                      </span>
                      <span className="m-approach-facts">
                        <span><strong>{hoursAhead(hoursFromNow(c.tca, wallNow()))}</strong><small>{utc(c.tca)}</small></span>
                        <span><strong>{km(c.miss_distance_km)}</strong><small>miss distance</small></span>
                      </span>
                      <span className="m-approach-risk">
                        <Risk value={c.risk_probability_estimate ?? null} operating={op} status={c.estimate_status} />
                      </span>
                    </button>
                  </li>
                );
              })}
            </ul>
          </div>
        )}
      </div>
    </div>
  );
}
