// One object's identity, orbit summary, data age and its upcoming close approaches.
import { useState } from "react";
import { query, useApi, type Meta, type ObjectDetail, type Schemas } from "../api";
import { ageDays, hoursAhead, hoursFromNow, km, objectTypeName, utc } from "../format";
import { setState, useStore, wallNow } from "../store";
import { Risk, focusApproach } from "./ConjunctionsView";

type ObjectPage = Schemas["ObjectPage"];

export function ObjectView() {
  const selected = useStore((s) => s.selected);
  const meta = useApi<Meta>("/v1/meta");
  const [q, setQ] = useState("");
  const [submitted, setSubmitted] = useState("");
  const search = useApi<ObjectPage>(submitted ? `/v1/objects${query({ q: submitted, limit: 10 })}` : null);
  const detail = useApi<ObjectDetail>(selected !== null ? `/v1/objects/${selected}` : null);
  const op = meta.data?.operating_probability ?? null;

  return (
    <div className="view">
      <h2>Object</h2>
      <form
        className="controls"
        role="search"
        onSubmit={(e) => {
          e.preventDefault();
          setSubmitted(q.trim());
        }}
      >
        <label>
          Find by name or NORAD id <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="e.g. ISS or 25544" />
        </label>
        <button type="submit">Search</button>
      </form>
      {search.data && (
        <ul className="results">
          {search.data.items.length === 0 && <li>No match in this snapshot.</li>}
          {search.data.items.map((o) => (
            <li key={o.norad_id}>
              <button type="button" className="row-button" onClick={() => setState({ selected: o.norad_id, focus: null })}>
                {o.name} <span className="muted">({o.norad_id}, {objectTypeName(o.object_type)})</span>
              </button>
            </li>
          ))}
        </ul>
      )}

      {selected === null && <p className="muted">Click an object on the globe, or search for one.</p>}
      {detail.loading && <p>Loading…</p>}
      {detail.error && <p className="error">{detail.error.message}</p>}
      {detail.data && (
        <section className="detail">
          <h3>{detail.data.name}</h3>
          <dl className="facts">
            <dt>Type</dt>
            <dd>
              {objectTypeName(detail.data.object_type)}
              {detail.data.rcs_size && `, radar size ${detail.data.rcs_size.toLowerCase()}`}
            </dd>
            <dt>NORAD id / designator</dt>
            <dd>
              {detail.data.norad_id} / {detail.data.object_id ?? "unknown"}
            </dd>
            {detail.data.owner && (
              <>
                <dt>Owner</dt>
                <dd>{detail.data.owner}</dd>
              </>
            )}
            {detail.data.launch_date && (
              <>
                <dt>Launched</dt>
                <dd>{detail.data.launch_date.slice(0, 10)}</dd>
              </>
            )}
            <dt>Orbit</dt>
            <dd>
              {detail.data.perigee_km.toFixed(0)} × {detail.data.apogee_km.toFixed(0)} km, {detail.data.inclination_deg.toFixed(1)}°, period{" "}
              {detail.data.period_min.toFixed(1)} min
            </dd>
            <dt>Orbit data</dt>
            <dd>
              epoch {utc(detail.data.epoch)} ({ageDays(detail.data.epoch_age_days)} at the snapshot)
            </dd>
          </dl>
          <h4>Close approaches in the next 7 days ({detail.data.conjunctions.length})</h4>
          {detail.data.conjunctions.length === 0 && <p className="muted">None within 5 km with objects of other constellations.</p>}
          <ul className="results">
            {detail.data.conjunctions.slice(0, 50).map((c) => {
              const other = c.objects.find((o) => o.norad_id !== detail.data!.norad_id) ?? c.objects[1];
              return (
                <li key={c.id}>
                  <button
                    type="button"
                    className="row-button"
                    onClick={() => {
                      focusApproach(c);
                      window.location.hash = "/conjunctions";
                    }}
                  >
                    {other.name ?? other.norad_id}: {km(c.miss_distance_km)} {hoursAhead(hoursFromNow(c.tca, wallNow()))}
                  </button>{" "}
                  <Risk value={c.risk_probability_estimate ?? null} operating={op} status={c.estimate_status} />
                </li>
              );
            })}
          </ul>
        </section>
      )}
    </div>
  );
}
