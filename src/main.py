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
import logging
import sys

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

    print(format_summary(catalog_summary(settings.processed_dir)))
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
