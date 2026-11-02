// Upcoming close approaches as cards: sort and filter chips, more cards load as the
// list scrolls, and a card opens the approach's detail in a sheet.
import { useEffect, useMemo, useRef, useState } from "react";
import { get, query, useApi, type Conjunction, type ConjunctionDetail, type ConjunctionPage, type Meta } from "../api";
import { hoursAhead, hoursFromNow, km } from "../format";
import { TYPE_COLORS, type ObjectType } from "../orbits";
import { wallNow } from "../store";
import { Detail, Risk, focusApproach } from "../views/ConjunctionsView";
import { WatchButton, clockParts, useUtcTimes } from "./GlobeScreen";
import { Icon } from "./icons";
import { navigate } from "./nav";
import { PullToRefresh } from "./PullToRefresh";
import { Sheet } from "./Sheet";
import { checkForNewData } from "./toast";

const PAGE = 30;
type Sort = "probability" | "tca" | "miss";
const SORTS: [Sort, string][] = [
  ["probability", "Highest risk"],
  ["tca", "Soonest"],
  ["miss", "Closest"],
];

const dot = (t: string | null) => TYPE_COLORS[t && t in TYPE_COLORS ? (t as ObjectType) : "UNK"];

export function ApproachesScreen({ meta, openId }: { meta?: Meta; openId: string | null }) {
  const [sort, setSort] = useState<Sort>("probability");
  const [maxMiss, setMaxMiss] = useState<number | null>(null);
  const [scoredOnly, setScoredOnly] = useState(false);
  const [showPast, setShowPast] = useState(false);
  const [items, setItems] = useState<Conjunction[]>([]);
  const [total, setTotal] = useState<number | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [open, setOpen] = useState<string | null>(openId);
  const sentinel = useRef<HTMLDivElement>(null);
  const op = meta?.operating_probability ?? null;
  const inUtc = useUtcTimes();

  useEffect(() => {
    if (openId) setOpen(openId);
  }, [openId]);

  // Upcoming only: from the real time when the filters were set, to the minute.
  const base = useMemo(() => {
    const from = showPast ? undefined : new Date(Math.floor(wallNow() / 60_000) * 60_000).toISOString();
    return { sort, max_miss_km: maxMiss ?? undefined, scored_only: scoredOnly || undefined, tca_from: from, limit: PAGE };
  }, [sort, maxMiss, scoredOnly, showPast]);

  const load = useRef<(offset: number) => void>(() => {});
  load.current = (offset: number) => {
    setLoading(true);
    get<ConjunctionPage>(`/v1/conjunctions${query({ ...base, offset })}`).then(
      (p) => {
        setItems((prev) => (offset === 0 ? p.items : [...prev, ...p.items]));
        setTotal(p.total);
        setError(null);
        setLoading(false);
      },
      (e: Error) => {
        setError(e.message);
        setLoading(false);
      },
    );
  };

  useEffect(() => {
    setItems([]);
    setTotal(null);
    load.current(0);
  }, [base]);

  // The next page loads as the end of the list comes into view.
  useEffect(() => {
    const el = sentinel.current;
    if (!el || typeof IntersectionObserver === "undefined") return;
    const io = new IntersectionObserver((entries) => {
      if (entries[0].isIntersecting && !loading && total !== null && items.length < total) load.current(items.length);
    }, { rootMargin: "400px" });
    io.observe(el);
    return () => io.disconnect();
  }, [items.length, total, loading]);

  return (
    <PullToRefresh onRefresh={() => checkForNewData().then(() => undefined)} label="Close approaches">
      <header className="m-screen-head">
        <h1>Close approaches</h1>
        <p className="m-muted">
          Estimated from public orbit data for the 7 days after the snapshot
          {total !== null ? ` · ${total.toLocaleString()} ${showPast ? "in total" : "upcoming"}` : ""}.
        </p>
      </header>

      <div className="m-chips" role="toolbar" aria-label="Sort and filter">
        {SORTS.map(([s, label]) => (
          <button key={s} type="button" className="m-chip" aria-pressed={sort === s} onClick={() => setSort(s)}>
            {label}
          </button>
        ))}
        <span className="m-chip-gap" aria-hidden />
        {[1, 2].map((d) => (
          <button key={d} type="button" className="m-chip" aria-pressed={maxMiss === d} onClick={() => setMaxMiss(maxMiss === d ? null : d)}>
            Under {d} km
          </button>
        ))}
        <button type="button" className="m-chip" aria-pressed={scoredOnly} onClick={() => setScoredOnly(!scoredOnly)}>
          With an estimate
        </button>
        <button type="button" className="m-chip" aria-pressed={showPast} onClick={() => setShowPast(!showPast)}>
          Include past
        </button>
      </div>

      {meta && !meta.history_complete && meta.model_version && (
        <p className="m-note">
          <Icon name="info" size={16} /> Provisional estimates: under 14 days of update history this run.
        </p>
      )}

      {error && items.length === 0 && (
        <div className="m-empty">
          <p>Could not load close approaches.</p>
          <button type="button" className="m-button" onClick={() => load.current(0)}>
            Try again
          </button>
        </div>
      )}
      {!error && total === 0 && <div className="m-empty"><p>No close approaches match these filters.</p></div>}

      <ul className="m-cards">
        {items.map((c) => {
          const t = clockParts(new Date(c.tca).getTime(), inUtc);
          return (
            <li key={c.id}>
              <button type="button" className="m-card m-approach" onClick={() => setOpen(c.id)}>
                <span className="m-approach-names">
                  <span><span className="m-type-dot" style={{ background: dot(c.objects[0].object_type) }} aria-hidden />{c.objects[0].name ?? c.objects[0].norad_id}</span>
                  <span><span className="m-type-dot" style={{ background: dot(c.objects[1].object_type) }} aria-hidden />{c.objects[1].name ?? c.objects[1].norad_id}</span>
                </span>
                <span className="m-approach-facts">
                  <span>
                    <strong>{hoursAhead(hoursFromNow(c.tca, wallNow()))}</strong>
                    <small>{t.date}, {t.time}</small>
                  </span>
                  <span>
                    <strong>{km(c.miss_distance_km)}</strong>
                    <small>miss distance</small>
                  </span>
                </span>
                <span className="m-approach-risk">
                  <Risk value={c.risk_probability_estimate ?? null} operating={op} status={c.estimate_status} />
                </span>
              </button>
            </li>
          );
        })}
        {!items.length && !error && total === null && [0, 1, 2, 3].map((k) => <li key={k} className="m-card m-skeleton" aria-hidden />)}
      </ul>
      <div ref={sentinel} className="m-sentinel">{loading && items.length > 0 ? "Loading more…" : ""}</div>

      <Sheet open={open !== null} onClose={() => setOpen(null)} title="Close approach" tall>
        {open && <ApproachDetail id={open} operating={op} close={() => setOpen(null)} />}
      </Sheet>
    </PullToRefresh>
  );
}

function ApproachDetail({ id, operating, close }: { id: string; operating: number | null; close: () => void }) {
  const d = useApi<ConjunctionDetail>(`/v1/conjunctions/${encodeURIComponent(id)}`);
  const c = d.data;
  return (
    <div className="m-detail">
      {c && (
        <div className="m-actions sticky">
          <button
            type="button"
            className="m-button primary"
            onClick={() => {
              close();
              void navigate("globe").then(() => focusApproach(c as unknown as Conjunction));
            }}
          >
            <Icon name="globe" size={18} /> Show on globe
          </button>
          <ShareButton title={`${c.object_details[0].name} × ${c.object_details[1].name}`} text={`Estimated close approach: ${km(c.miss_distance_km)}, from public orbit data.`} />
        </div>
      )}
      <Detail id={id} operating={operating} />
      {c && (
        <div className="m-actions">
          {c.object_details.map((o) => (
            <WatchButton key={o.norad_id} norad={o.norad_id} name={o.name} compact />
          ))}
        </div>
      )}
    </div>
  );
}

export function ShareButton({ title, text }: { title: string; text: string }) {
  if (typeof navigator === "undefined" || !navigator.share) return null;
  return (
    <button
      type="button"
      className="m-button ghost"
      onClick={() => navigator.share({ title, text, url: window.location.href.replace("?app=1", "") }).catch(() => undefined)}
    >
      <Icon name="share" size={18} /> Share
    </button>
  );
}
