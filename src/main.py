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
    trn.add_argument("--workers", type=int, default=ScreenConfig().workers)
    imp = commands.add_parser("import-spacetrack", help="import a Space-Track yearly TLE zip into history")
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

        res = run_train(settings, DatasetConfig(workers=args.workers), reuse=args.reuse, every_days=args.every_days)
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
