// Ranked table of upcoming close approaches. Selecting a row jumps the clock to the
// closest approach, flies the camera there and draws the line between the objects.
import { useEffect, useState } from "react";
import { get, query, useApi, type Conjunction, type ConjunctionDetail, type ConjunctionPage, type Meta } from "../api";
import { ageDays, hoursAhead, hoursFromNow, km, objectTypeName, percent, riskLabel, utc } from "../format";
import { setState, time, useStore, wallNow } from "../store";

/** Select a close approach: forecast mode, paused at its closest approach. */
export function focusApproach(c: Conjunction): void {
  const tca = new Date(c.tca).getTime();
  setState({ focus: { conjunctionId: c.id, norads: [c.objects[0].norad_id, c.objects[1].norad_id], tca }, selected: null });
  time.seek(tca);
}

const PAGE = 25;

export function ConjunctionsView({ meta }: { meta?: Meta }) {
  const [sort, setSort] = useState<"probability" | "tca" | "miss">("probability");
  const [maxMiss, setMaxMiss] = useState<number | "">("");
  const [scoredOnly, setScoredOnly] = useState(false);
  const [showPast, setShowPast] = useState(false);
  // Upcoming only: from the real time, rounded to the minute so the request stays cacheable.
  const from = showPast ? undefined : new Date(Math.floor(wallNow() / 60_000) * 60_000).toISOString();
  const [offset, setOffset] = useState(0);
  const focus = useStore((s) => s.focus);
  const page = useApi<ConjunctionPage>(
    `/v1/conjunctions${query({ sort, max_miss_km: maxMiss || undefined, scored_only: scoredOnly || undefined, tca_from: from, limit: PAGE, offset })}`,
  );
  const op = meta?.operating_probability ?? null;

  useEffect(() => setOffset(0), [sort, maxMiss, scoredOnly, showPast]);

  useEffect(() => {
    // Objects in approaches above the operating point, for the "risk" colour mode.
    if (op === null) return;
    get<ConjunctionPage>(`/v1/conjunctions${query({ min_probability: op, limit: 500 })}`).then((p) =>
      setState({ riskNorads: new Set(p.items.flatMap((c) => c.objects.map((o) => o.norad_id))) }),
    );
  }, [op]);

  const pick = (c: Conjunction) => focusApproach(c);

  return (
    <div className="view">
      <h2>Upcoming close approaches</h2>
      <p className="muted">
        Estimated from public orbit data for the 7 days after the snapshot.
        {meta && ` Same-constellation pairs (${meta.same_constellation_left_out.toLocaleString()}) are left out: most are artefacts of unmodelled station-keeping.`}
      </p>
      <div className="controls">
        <label>
          Sort by{" "}
          <select value={sort} onChange={(e) => setSort(e.target.value as typeof sort)}>
            <option value="probability">risk estimate</option>
            <option value="tca">time of closest approach</option>
            <option value="miss">miss distance</option>
          </select>
        </label>
        <label>
          Miss under{" "}
          <select value={maxMiss} onChange={(e) => setMaxMiss(e.target.value === "" ? "" : Number(e.target.value))}>
            <option value="">any</option>
            <option value={1}>1 km</option>
            <option value={2}>2 km</option>
          </select>
        </label>
        <label>
          <input type="checkbox" checked={scoredOnly} onChange={(e) => setScoredOnly(e.target.checked)} /> with an estimate only
        </label>
        <label>
          <input type="checkbox" checked={showPast} onChange={(e) => setShowPast(e.target.checked)} /> include past ones
        </label>
      </div>

      {page.loading && !page.data && <p>Loading…</p>}
      {page.error && <p className="error">Could not load close approaches: {page.error.message}</p>}
      {page.data && page.data.items.length === 0 && <p>No close approaches match these filters.</p>}
      {page.data && page.data.items.length > 0 && (
        <>
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Objects</th>
                <th scope="col">Closest approach</th>
                <th scope="col" className="num">Miss</th>
                <th scope="col">
                  Risk estimate
                  <div className="muted small th-note">chance it still looks &lt;5 km with newer data</div>
                </th>
              </tr>
            </thead>
            <tbody>
              {page.data.items.map((c) => (
                <tr key={c.id} className={focus?.conjunctionId === c.id ? "active" : undefined}>
                  <td>
                    <button type="button" className="row-button" onClick={() => pick(c)}>
                      {c.objects[0].name ?? c.objects[0].norad_id}
                      <span className="muted"> × </span>
                      {c.objects[1].name ?? c.objects[1].norad_id}
                    </button>
                  </td>
                  <td>
                    {hoursAhead(hoursFromNow(c.tca, wallNow()))}
                    <div className="muted small">{utc(c.tca)}</div>
                  </td>
                  <td className="num">{km(c.miss_distance_km)}</td>
                  <td>
                    <Risk value={c.risk_probability_estimate ?? null} operating={op} status={c.estimate_status} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          <div className="pager">
            <button type="button" disabled={offset === 0} onClick={() => setOffset(Math.max(0, offset - PAGE))}>
              Previous
            </button>
            <span>
              {offset + 1}–{offset + page.data.items.length} of {page.data.total.toLocaleString()}
            </span>
            <button type="button" disabled={offset + PAGE >= page.data.total} onClick={() => setOffset(offset + PAGE)}>
              Next
            </button>
          </div>
        </>
      )}
      {focus && <Detail id={focus.conjunctionId} operating={op} />}
    </div>
  );
}

export const PROVISIONAL_TIP =
  "Provisional: this run had under 14 days of update history. In testing, estimates made without history ran at about half the observed rate.";

export function Risk({
  value,
  operating,
  status = "standard",
}: {
  value: number | null;
  operating: number | null;
  status?: Conjunction["estimate_status"];
}) {
  if (value === null || status === "none") return <span className="muted small">Outside model scope</span>;
  const elevated = operating !== null && value >= operating;
  const provisional = status === "provisional";
  return (
    <span className={`risk ${elevated ? "elevated" : ""}`} title={provisional ? PROVISIONAL_TIP : riskLabel(value, operating)}>
      <span className="bar" aria-hidden>
        <span style={{ width: `${Math.max(2, value * 100)}%` }} />
      </span>
      {percent(value)}
      <span className="sr-only">, {riskLabel(value, operating)}{provisional ? ", provisional" : ""}</span>
      {elevated && <span className="tag">elevated</span>}
      {provisional && <span className="tag provisional" aria-hidden>provisional</span>}
    </span>
  );
}

function Detail({ id, operating }: { id: string; operating: number | null }) {
  const d = useApi<ConjunctionDetail>(`/v1/conjunctions/${encodeURIComponent(id)}`);
  if (d.loading) return <p>Loading the approach…</p>;
  if (d.error || !d.data) return <p className="error">Could not load this approach.</p>;
  const c = d.data;
  return (
    <section className="detail" aria-label="Selected close approach">
      <h3>
        {c.object_details[0].name} × {c.object_details[1].name}
      </h3>
      <dl className="facts">
        <dt>Closest approach</dt>
        <dd>{utc(c.tca)}</dd>
        <dt>Predicted miss</dt>
        <dd>
          {km(c.miss_distance_km, 3)} at {c.relative_speed_kms.toFixed(1)} km/s
        </dd>
        <dt>Risk estimate</dt>
        <dd>
          <Risk value={c.risk_probability_estimate ?? null} operating={operating} status={c.estimate_status} />
          <div className="muted small">
            Chance it still looks closer than 5 km once newer public data arrives. Not a collision probability.
          </div>
        </dd>
        {c.object_details.map((o) => (
          <div key={o.norad_id} className="fact-pair">
            <dt>{o.name}</dt>
            <dd>
              {objectTypeName(o.object_type)}, NORAD {o.norad_id}, orbit data {ageDays(o.epoch_age_days)}
            </dd>
          </div>
        ))}
      </dl>
      <p className="note">{c.confidence_note}</p>
      {c.top_reasons.length > 0 && (
        <>
          <h4>What drives this estimate</h4>
          <ul className="reasons">
            {c.top_reasons.map((r) => (
              <li key={r.feature}>
                <code>{r.feature}</code> {r.contribution >= 0 ? "raises" : "lowers"} it ({r.contribution >= 0 ? "+" : ""}
                {r.contribution.toFixed(2)} log-odds)
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
