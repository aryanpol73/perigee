// Method and limits in plain language: what the data is, why it is inaccurate,
// how the model works, what the numbers mean and do not mean.
import { useApi, type Meta, type ModelSummary } from "../api";
import { percent } from "../format";

export function MethodView({ meta }: { meta?: Meta }) {
  const model = useApi<ModelSummary>("/v1/model");
  const m = model.data;
  return (
    <div className="view prose">
      <h2>Method and limits</h2>
      <h3>The data</h3>
      <p>
        Every object's orbit comes from public element sets published by CelesTrak (and, for history, Space-Track.org). An element set
        describes one orbit at one moment, its epoch. Positions at other times come from the standard SGP4 model, computed in your
        browser for the globe.
      </p>
      <h3>Why it is inaccurate</h3>
      <p>
        Public orbit data is accurate to roughly a kilometre at its epoch, and the error grows every day after, especially in low orbit
        where air drag is hard to predict. There is no uncertainty (covariance) in public data. So Perigee never says two objects will
        collide: a predicted 800 m miss can easily be a real 5 km miss, or the reverse.
      </p>
      <h3>Close approaches</h3>
      <p>
        The whole catalog is searched for pairs passing within 5 km over the next 7 days. The search was checked against CelesTrak's
        SOCRATES list: on identical inputs it finds all of SOCRATES's approaches. Pairs within one constellation are left out because
        their station-keeping is not modelled, which makes most of them artefacts.
      </p>
      <h3>The risk estimate</h3>
      <p>
        A machine-learning model (LightGBM) estimates, for each approach up to 3 days ahead, the chance that it still looks closer than
        5 km once newer public data arrives. It was trained on 2025 history and tested on the last ten weeks of 2025, which it never
        saw.
      </p>
      {m && (
        <ul>
          <li>
            It ranks approaches far better than distance alone: PR-AUC {m.metrics.lightgbm?.pr_auc.toFixed(2)} against{" "}
            {m.metrics.b_tuned_miss_threshold?.pr_auc.toFixed(2)} for a distance threshold (higher is better; random is about 0.14).
          </li>
          {m.tradeoff && (
            <li>
              Of its 1,000 highest-scored test approaches, {percent(m.tradeoff.precision_at_top_k.find((t) => t.k === 1000)?.precision ?? null)} stayed close.
            </li>
          )}
          <li>"Elevated" means at or above {percent(m.operating_probability)}, the point chosen on validation data where about half of alerts are real.</li>
        </ul>
      )}
      {meta && !meta.history_complete && (
        <p className="note">
          This run's estimates are provisional. Four model inputs per object describe its last 30 days of orbit updates, and this
          run had under 14 days of them. Rescoring the test period that way (docs/history-sensitivity.md): with no history, the
          ranking score fell from 0.70 to 0.61 and estimates came out at about half the observed rate; with 15 or more days of
          snapshots every 2 to 6 hours they matched the full history.
        </p>
      )}
      <h3>What the numbers do not mean</h3>
      <ul>
        <li>A risk estimate is not a collision probability. "Newer data" is a better estimate, not the truth.</li>
        <li>A satellite that manoeuvres makes any estimate about its old path meaningless.</li>
        <li>The altitude risk index is relative (100 = riskiest band now), not a rate you can quote.</li>
        <li>The breakup simulator is illustrative and simplified: no drag, idealised geometry.</li>
      </ul>
      <p className="muted">Not for operational decisions. Data from CelesTrak and Space-Track.org (USSPACECOM).</p>
    </div>
  );
}
