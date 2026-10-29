"""
IRIS-OEK-RP command line.

    python -m app discover  [--source live|fixture] [--url URL] [--layer NAME]
    python -m app harvest   [--source live|fixture] [--page-size N] [--dry-run]
    python -m app validate
    python -m app export    [--out PATH] [--limit N]
"""

import argparse
import json
import logging
import sys
from pathlib import Path

from app.config.settings import Settings
from app.runners import compensation_runner as runner


def _settings(args: argparse.Namespace) -> Settings:
    overrides = {
        key: value
        for key, value in {
            "source_mode": getattr(args, "source", None),
            "wfs_url": getattr(args, "url", None),
            "wfs_layer": getattr(args, "layer", None),
            "page_size": getattr(args, "page_size", None),
        }.items()
        if value is not None
    }
    return Settings(**overrides)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m app", description="IRIS-OEK-RP compensation-site harvester")
    parser.add_argument("-v", "--verbose", action="store_true")
    sub = parser.add_subparsers(dest="command", required=True)

    for name in ("discover", "harvest"):
        command = sub.add_parser(name)
        command.add_argument("--source", choices=["live", "fixture"])
        command.add_argument("--url", help="WFS endpoint (overrides IRIS_WFS_URL)")
        command.add_argument("--layer", help="feature type (overrides IRIS_WFS_LAYER)")

    sub.choices["harvest"].add_argument("--page-size", type=int)
    sub.choices["harvest"].add_argument("--dry-run", action="store_true", help="no database writes")

    sub.add_parser("validate")

    export = sub.add_parser("export")
    export.add_argument("--out", type=Path, default=Path("examples/sample_export.geojson"))
    export.add_argument("--limit", type=int, default=25)

    args = parser.parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)-7s %(name)s: %(message)s",
    )

    settings = _settings(args)

    if args.command == "discover":
        print(json.dumps(runner.run_discover(settings), indent=2, ensure_ascii=False))
        return 0

    if args.command == "harvest":
        try:
            report = runner.run_harvest(settings, dry_run=args.dry_run)
        except Exception as exc:  # report already written; keep CLI output short
            print(f"Harvest failed: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1

        print(
            json.dumps(
                {
                    "run": report["run"],
                    "counts": report["counts"],
                    "database": report["database"],
                    "rejected_by_code": report["rejected_by_code"],
                },
                indent=2,
            )
        )
        return 0

    if args.command == "validate":
        results = runner.run_validate(settings)
        failed = [r for r in results if r["passed"] is False]

        for result in results:
            status = {True: "PASS", False: "FAIL", None: "INFO"}[result["passed"]]
            print(f"\n[{status}] {result['name']} ({len(result['rows'])} rows)")

            for row in result["rows"][:25]:
                print("   ", json.dumps(row, default=str, ensure_ascii=False))

        print(f"\n{len(results) - len(failed)}/{len(results)} checks without failure")
        return 1 if failed else 0

    if args.command == "export":
        count = runner.run_export(settings, args.out, args.limit)
        print(f"Wrote {count} features to {args.out}")
        return 0

    return 2


if __name__ == "__main__":
    raise SystemExit(main())
