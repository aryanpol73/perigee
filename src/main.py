"""Perigee command line.

  python -m src.main data         fetch what is due, rebuild the latest tables, print a summary
  python -m src.main data --offline   rebuild from the raw archive only, no network
  python -m src.main rebuild      rebuild every catalog snapshot from the raw archive
  python -m src.main summary      print the summary of what is stored

Exit codes: 0 ok, 1 a download was rejected or failed, 2 CelesTrak refused a
request (or an earlier refusal's STOP file is present) and a human must look.
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from src.config import Settings
from src.log import setup_logging
from src.pipeline import FAILED_STATUSES, rebuild_all, run_data
from src.screening.run import run_screen
from src.screening.screen import ScreenConfig
from src.storage.summary import catalog_summary, format_summary

log = logging.getLogger("perigee.cli")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="perigee")
    commands = parser.add_subparsers(dest="command", required=True)
    data = commands.add_parser("data", help="fetch due sources, rebuild tables, print summary")
    data.add_argument("--offline", action="store_true", help="do not download anything")
    commands.add_parser("rebuild", help="rebuild all processed tables from the raw archive")
    commands.add_parser("summary", help="print the stored catalog summary")
    scr = commands.add_parser("screen", help="screen the latest snapshot for close approaches")
    scr.add_argument("--days", type=float, default=7.0)
    scr.add_argument("--threshold-km", type=float, default=5.0)
    scr.add_argument("--step-s", type=float, default=20.0)
    scr.add_argument("--scope", choices=["active", "all"], default="active")
    scr.add_argument("--workers", type=int, default=ScreenConfig().workers)
    trn = commands.add_parser("train", help="rebuild the ML dataset from history, train and evaluate")
    trn.add_argument("--reuse", action="store_true", help="train on the stored dataset")
    trn.add_argument("--every-days", type=float, default=1.0, help="spacing of prediction times")
    trn.add_argument("--workers", type=int, default=6)
    trn.add_argument("--from", dest="start", help="first day of history to use (YYYY-MM-DD)")
    trn.add_argument("--to", dest="end", help="last day of history to use (YYYY-MM-DD)")
    shl = commands.add_parser("shells", help="compute the altitude-band risk index")
    shl.add_argument("--band-km", type=float, default=25.0)
    shl.add_argument("--at", help="use element-set history as known at this date (YYYY-MM-DD) instead of the latest snapshot")
    shl.add_argument("--size-mapping", choices=["default", "unknown_medium"], default="default")
    sim = commands.add_parser("simulate", help="run the illustrative breakup simulator")
    sim.add_argument("--altitude-km", type=float, default=800.0)
    sim.add_argument("--inclination-deg", type=float, default=74.0)
    sim.add_argument("--target-kg", type=float, default=950.0)
    sim.add_argument("--impactor-kg", type=float, default=560.0)
    sim.add_argument("--rel-speed", type=float, default=11.7, help="km/s")
    sim.add_argument("--target-kind", choices=["SC", "RB"], default="SC")
    sim.add_argument("--impactor-kind", choices=["SC", "RB"], default="SC")
    sim.add_argument("--seed", type=int, default=42)
    pub = commands.add_parser("publish", help="score the latest screen and publish the results bundle the API serves")
    pub.add_argument("--model", help="model version under data/models (default: newest)")
    pub.add_argument("--fixture", action="store_true", help="write the small committed fixture bundle instead")
    api = commands.add_parser("api", help="serve the read-only API")
    api.add_argument("--port", type=int, default=8000)
    api.add_argument("--fixture", action="store_true", help="serve the committed fixture bundle")
    commands.add_parser("dev", help="run the API and the web app together (fixture data if nothing is published)")
    imp =commands.add_parser("import-spacetrack", help="import a Space-Track yearly TLE zip into history")
    imp.add_argument("archive", type=Path, help="zip or text file downloaded once from space-track.org")
    imp.add_argument("--since", help="keep element sets from this date (YYYY-MM-DD)")
    imp.add_argument("--until", help="keep element sets before this date (YYYY-MM-DD)")
    imp.add_argument("--lag-hours", type=float, default=12.0, help="assumed publication lag after epoch")
    args = parser.parse_args(argv)

    run_id = setup_logging()
    settings = Settings.from_env()
    log.info("start", extra={"command": args.command, "data_dir": str(settings.data_dir)})

    exit_code = 0
    if args.command == "data":
        outcomes = run_data(settings, fetch=not args.offline)
        print(f"Run {run_id}")
        for outcome in outcomes:
            line = f"  {outcome.source:<14} {outcome.status:<14} {outcome.snapshot_id or '-'}"
            print(f"{line}  {outcome.detail}" if outcome.detail else line)
        statuses = {o.status for o in outcomes}
        if statuses & {"refused", "stopped"}:
            exit_code = 2
        elif statuses & FAILED_STATUSES:
            exit_code = 1
        print()
    elif args.command == "rebuild":
        built = rebuild_all(settings)
        print(f"Rebuilt {built} catalog snapshot(s) from the raw archive.\n")
    elif args.command == "screen":
        config = ScreenConfig(
            threshold_km=args.threshold_km,
            step_s=args.step_s,
            days=args.days,
            scope=args.scope,
            workers=args.workers,
        )
        result = run_screen(settings, config)
        s = result.summary()
        print(
            f"Screened {s['objects']:,} objects for {args.days:g} days from {s['start']} "
            f"({args.scope} scope, {args.threshold_km:g} km): {s['events']:,} close approaches "
            f"({s['colocated_pairs']} co-located pairs) in {s['seconds']:.0f} s on {args.workers} worker(s)."
        )
        print("Estimated close approaches based on public data; not collision predictions.")
        return exit_code
    elif args.command == "shells":
        import pandas as pd

        from src.risk.run import run_shells
        from src.risk.shells import ShellConfig

        at = pd.Timestamp(args.at, tz="UTC") if args.at else None
        res = run_shells(settings, ShellConfig(band_km=args.band_km, size_mapping=args.size_mapping), at)
        print(f"Shell risk index {res['run_id'].iloc[0]} ({res['objects'].sum():,.0f} objects in range):")
        for r in res.nlargest(5, "risk_index").itertuples():
            print(f"  {r.alt_lo_km:>6.0f}-{r.alt_hi_km:<6.0f} km  index {r.risk_index:6.1f}  weighted {r.risk_index_weighted:6.1f}  objects {r.objects:7.0f}")
        print("A relative index from public data, not a collision probability.")
        return exit_code
    elif args.command == "simulate":
        from src.sim.breakup import Collision, simulate, spread

        b = simulate(
            Collision(
                altitude_km=args.altitude_km,
                inclination_deg=args.inclination_deg,
                target_mass_kg=args.target_kg,
                impactor_mass_kg=args.impactor_kg,
                rel_speed_km_s=args.rel_speed,
                target_kind=args.target_kind,
                impactor_kind=args.impactor_kind,
                seed=args.seed,
            )
        )
        print(json.dumps(b.summary(), indent=2))
        for hours in (0.25, 6, 24, 24 * 30, 24 * 365):
            s = spread(b.fragments, hours)
            print(
                f"  after {hours:>6g} h: cloud radius {s['cloud_radius_km']:6.0f} km, along-track coverage "
                f"{s['along_track_coverage']:.0%}, node coverage {s['node_coverage']:.0%}"
            )
        return exit_code
    elif args.command == "publish":
        from src.results.bundle import build
        from src.results.fixture import FIXTURE_DIR, fixture_selection

        if args.fixture:
            b = build(settings, args.model, fixture_selection(settings), FIXTURE_DIR, min_objects=100)
        else:
            b = build(settings, args.model)
        m = b.meta
        print(
            f"Published {m['run_id']} to {b.path}: {m['object_count']:,} objects, {m['conjunction_count']:,} close "
            f"approaches ({m['scored_conjunctions']:,} scored by model {m['model_version']})."
        )
        if not m["history_complete"]:
            print(f"Note: only {m['history_days_for_features']} days of update history before the snapshot; scores are flagged.")
        return exit_code
    elif args.command == "api":
        import uvicorn

        from src.api.app import create_app
        from src.results.fixture import FIXTURE_DIR

        uvicorn.run(create_app(settings, FIXTURE_DIR if args.fixture else None), host="127.0.0.1", port=args.port, log_config=None)
        return exit_code
    elif args.command == "dev":
        from src.dev import run_dev

        return run_dev(settings)
    elif args.command == "import-spacetrack":
        import pandas as pd

        from src.ingest.spacetrack_import import import_archive, sanity_check

        to_ts = lambda s: pd.Timestamp(s, tz="UTC") if s else None
        counts = import_archive(
            args.archive, settings.processed_dir, to_ts(args.since), to_ts(args.until), pd.Timedelta(hours=args.lag_hours)
        )
        print(json.dumps({"imported": counts, "history": sanity_check(settings.processed_dir)}, indent=2))
        print("Cite in anything published: Data from Space-Track.org (USSPACECOM).")
        return exit_code
    elif args.command == "train":
        from src.ml.dataset import DatasetConfig
        from src.ml.pipeline import run_train

        import pandas as pd

        to_ts = lambda s: pd.Timestamp(s, tz="UTC") if s else None
        res = run_train(
            settings,
            DatasetConfig(workers=args.workers),
            reuse=args.reuse,
            every_days=args.every_days,
            start=to_ts(args.start),
            end=to_ts(args.end),
        )
        if res["status"] == "insufficient_data":
            print(f"Not enough labelled history to train honestly: {res['reason']}")
            print(json.dumps(res["counts"], indent=2))
            return 3
        lgbm = res["models"]["lightgbm"]
        print(f"Model {res['model_version']} on {res['data_window'][0]} .. {res['data_window'][1]}")
        for name, m in res["models"].items():
            print(f"  {name:<45} PR-AUC {m['pr_auc']:.3f}  CI {m['pr_auc_ci95'][0]:.3f}-{m['pr_auc_ci95'][1]:.3f}")
        print(f"LightGBM beats all baselines: {res['lightgbm_beats_all_baselines']} (best baseline {res['best_baseline']})")
        print(f"Saved to {res['saved_to']}; model card at docs/model-card.md. Best iteration {lgbm['best_iteration']}.")
        return exit_code

    print(format_summary(catalog_summary(settings.processed_dir)))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
