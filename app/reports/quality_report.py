"""
Per-run quality / error report (JSON for machines, Markdown for people).

    data/runs/<run>/quality_report.json
    data/runs/<run>/quality_report.md
"""

import json
from collections import Counter
from dataclasses import asdict
from pathlib import Path
from typing import Any

from app.loaders.compensation_loader import LoadResult
from app.transformers.compensation_transformer import TransformResult

COMPLETENESS_COLUMNS = (
    "object_type",
    "site_name",
    "file_reference",
    "municipality_key",
    "municipality_name",
    "district_name",
    "cadastral_district_key",
    "cadastral_district_name",
    "compensation_status",
    "area_source_m2",
    "eco_credits_total",
    "eco_credits_available",
    "registering_authority",
    "registered_on",
)


def build_report(
    *,
    run: dict[str, Any],
    discovery: dict[str, Any] | None,
    transform: TransformResult | None,
    load: LoadResult | None,
    raw_files: list[dict[str, Any]],
) -> dict[str, Any]:
    report: dict[str, Any] = {"run": run, "provenance": {"discovery": discovery, "raw_files": raw_files}}

    if transform is None:
        return report

    counts = dict(transform.counts)
    counts.setdefault("duplicates_collapsed", 0)
    counts.setdefault("rejected", 0)
    counts.setdefault("accepted", 0)
    counts["reconciles"] = counts.get("fetched", 0) == (
        counts["accepted"] + counts["rejected"] + counts["duplicates_collapsed"]
    )

    rejected = [i for i in transform.issues if i.severity == "rejected"]
    warnings = [i for i in transform.issues if i.severity == "warning"]
    total = len(transform.records) or 1

    report["counts"] = counts
    report["database"] = asdict(load) if load else None
    report["rejected_by_code"] = dict(Counter(i.code for i in rejected).most_common())
    report["warnings_by_code"] = dict(Counter(i.code for i in warnings).most_common())
    report["rejected_records"] = [
        {"site_id": i.site_id, "feature_id": i.feature_id, "stage": i.stage, "code": i.code, "message": i.message}
        for i in rejected
    ]
    report["warning_records"] = [{"site_id": i.site_id, "code": i.code, "message": i.message} for i in warnings]
    report["attribute_completeness_pct"] = {
        column: round(100 * sum(getattr(r, column) is not None for r in transform.records) / total, 1)
        for column in COMPLETENESS_COLUMNS
    }

    return report


def render_markdown(report: dict[str, Any]) -> str:
    run = report["run"]
    discovery = report["provenance"]["discovery"] or {}
    lines = [
        f"# Quality report - run `{run['run_id']}`",
        "",
        f"- **Status:** {run['status']}",
        f"- **Source mode:** {run['source_mode']}",
        f"- **Endpoint:** `{run['endpoint']}`",
        f"- **Layer:** `{discovery.get('feature_type', run.get('layer'))}`",
        f"- **Started / finished (UTC):** {run['started_at']} / {run.get('finished_at')}",
    ]

    if run.get("error"):
        lines += ["", "## Failure", "", f"```\n{run['error']}\n```"]

    if discovery:
        lines += [
            "",
            "## Provenance",
            "",
            "| Item | Value |",
            "|---|---|",
            f"| Service | {discovery.get('service_title')} ({discovery.get('provider')}) |",
            f"| WFS version | {discovery.get('wfs_version')} |",
            f"| Licence / fees | {discovery.get('fees_licence')} |",
            f"| Access constraints | {discovery.get('access_constraints')} |",
            f"| Source CRS | {discovery.get('default_crs')} |",
            f"| Output format | {discovery.get('output_format')} |",
            f"| Paging | ResultPaging={discovery.get('implements_result_paging')}, "
            f"TransactionSafe={discovery.get('paging_is_transaction_safe')}, "
            f"CountDefault={discovery.get('count_default')}, page size={discovery.get('page_size')} |",
            f"| Sort | {discovery.get('sort_by')} |",
            f"| numberMatched | {discovery.get('number_matched')} |",
            f"| Mapped fields missing in schema | {discovery.get('mapped_fields_missing_in_schema')} |",
            f"| Schema fields not mapped (kept in source_attributes) | {discovery.get('schema_fields_not_mapped')} |",
            f"| Verify-before-use blockers (permissions / schema) | {discovery.get('harvest_blockers') or 'none'} |",
        ]

    if report["provenance"]["raw_files"]:
        lines += ["", "### Raw responses", "", "| File | Bytes | SHA-256 |", "|---|---:|---|"]
        lines += [
            f"| {f['file']} | {f['bytes']:,} | `{f['sha256'][:16]}...` |" for f in report["provenance"]["raw_files"]
        ]

    if "counts" not in report:
        return "\n".join(lines) + "\n"

    counts = report["counts"]
    database = report["database"] or {}

    lines += [
        "",
        "## Counts by processing outcome",
        "",
        "| Outcome | Records |",
        "|---|---:|",
        f"| Fetched from source | {counts.get('fetched', 0)} |",
        f"| Accepted | {counts['accepted']} |",
        f"| - of which with quality warnings | {counts.get('accepted_with_warnings', 0)} |",
        f"| - of which geometry repaired | {counts.get('accepted_geometry_repaired', 0)} |",
        f"| Rejected | {counts['rejected']} |",
        f"| Identical duplicates collapsed | {counts['duplicates_collapsed']} |",
        f"| **Reconciles (fetched = accepted + rejected + collapsed)** | **{counts['reconciles']}** |",
    ]

    if database:
        lines += [
            f"| DB inserted | {database['inserted']} |",
            f"| DB updated | {database['updated']} |",
            f"| DB unchanged (idempotent no-op) | {database['unchanged']} |",
            f"| In staging but not seen this run | {database['not_seen_in_run']} |",
        ]

    for title, key in (("Rejected by reason", "rejected_by_code"), ("Warnings by reason", "warnings_by_code")):
        lines += ["", f"## {title}", "", "| Code | Count |", "|---|---:|"]
        lines += [f"| `{code}` | {n} |" for code, n in report[key].items()] or ["| - | 0 |"]

    lines += ["", "## Rejected records", "", "| site_id | Stage | Code | Detail |", "|---|---|---|---|"]
    lines += [
        f"| {r['site_id'] or '-'} | {r['stage']} | `{r['code']}` | {r['message']} |" for r in report["rejected_records"]
    ] or ["| - | - | - | - |"]

    lines += ["", "## Attribute completeness (accepted records)", "", "| Column | Non-null % |", "|---|---:|"]
    lines += [f"| {column} | {pct} |" for column, pct in report["attribute_completeness_pct"].items()]

    return "\n".join(lines) + "\n"


def write_report(report: dict[str, Any], run_dir: Path) -> tuple[Path, Path]:
    json_path = run_dir / "quality_report.json"
    md_path = run_dir / "quality_report.md"
    json_path.write_text(json.dumps(report, indent=2, default=str, ensure_ascii=False), encoding="utf-8")
    md_path.write_text(render_markdown(report), encoding="utf-8")
    return json_path, md_path
