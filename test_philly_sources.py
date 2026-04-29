"""Validate Philadelphia scraper sources.

Run from the project root:
    python test_philly_sources.py --sources li_violations inquirer_probate \\
        fjd_lis_pendens bid4assets_mortgage bid4assets_tax fjd_evictions \\
        --lookback 30
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict

sys.path.insert(0, "src")

import config  # noqa: E402
from notice_parser import NoticeData  # noqa: E402
from philadelphia_scrapers import run_philly_scrape  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("test_philly")

DEFAULT_ORDER = [
    "li_violations",
    "inquirer_probate",
    "fjd_lis_pendens",
    "bid4assets_mortgage",
    "bid4assets_tax",
    "fjd_evictions",
]

# Core NoticeData fields shown in preview
PREVIEW_FIELDS = [
    "date_added",
    "address",
    "zip",
    "owner_name",
    "decedent_name",
    "notice_type",
    "auction_date",
    "parcel_id",
    "property_type",
    "estimated_value",
    "year_built",
    "source_url",
]


def _truncate(s: str, n: int = 60) -> str:
    return s if len(s) <= n else s[: n - 1] + "…"


def _meta(n: NoticeData) -> dict:
    try:
        return json.loads(n.heir_map_json) if n.heir_map_json else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _print_source_results(
    source_id: str,
    notices: list[NoticeData],
    dropped: int,
) -> None:
    source = next((s for s in config.PHILLY_SOURCES if s.source_id == source_id), None)
    desc = source.description if source else source_id
    before = len(notices) + dropped

    bar = "=" * 72
    print(f"\n{bar}")
    print(f"  SOURCE : {source_id}")
    print(f"  DESC   : {desc}")
    print(f"  BEFORE Filter 2 : {before}")
    print(f"  AFTER  Filter 2 : {len(notices)}  ({dropped} dropped as vacant/land)")
    print(bar)

    if not notices:
        print("  (no records)")
        return

    preview = notices[:5]
    print(f"  Showing first {len(preview)} of {len(notices)} records:\n")

    for i, n in enumerate(preview, 1):
        d = asdict(n)
        meta = _meta(n)
        print(f"  [{i}]")
        for fld in PREVIEW_FIELDS:
            val = str(d.get(fld) or "")
            if val:
                print(f"      {fld:<22} {_truncate(val)}")
        # Show structured metadata from heir_map_json
        if meta:
            for k, v in meta.items():
                if v not in (None, "", False):
                    print(f"      meta.{k:<17} {_truncate(str(v))}")
        print()

    # Probate-specific: OPA match rate
    if source_id == "inquirer_probate":
        matched = sum(1 for n in notices if _meta(n).get("opa_match") is True)
        total = len(notices)
        print(f"  OPA match rate: {matched}/{total} ({100*matched//total if total else 0}%)\n")


def _print_summary(
    results: dict[str, list[NoticeData]],
    filter2_dropped: dict[str, int],
) -> None:
    print("\n" + "=" * 72)
    print("  SUMMARY")
    print("=" * 72)
    print(f"  {'Source':<32} {'Before F2':>9} {'Dropped':>8} {'After F2':>9}")
    print(f"  {'-'*32} {'-'*9} {'-'*8} {'-'*9}")
    grand_total = 0
    for sid in DEFAULT_ORDER:
        if sid not in results:
            continue
        kept = len(results[sid])
        dropped = filter2_dropped.get(sid, 0)
        before = kept + dropped
        status = "✓" if kept else "○"
        print(f"  {status} {sid:<30} {before:>9} {dropped:>8} {kept:>9}")
        grand_total += kept
    print(f"\n  Total records after both filters: {grand_total}")


async def main(
    source_ids: list[str],
    lookback_days: int,
    checkpoint_file: str | None = None,
) -> None:
    from pathlib import Path

    print(f"\nPhiladelphia scraper — lookback {lookback_days} days")
    print(f"Sources: {', '.join(source_ids)}\n")
    if checkpoint_file:
        print(f"Resuming fjd_evictions from checkpoint: {checkpoint_file}\n")

    ckpt = Path(checkpoint_file) if checkpoint_file else None
    payload = await run_philly_scrape(
        source_ids=source_ids,
        lookback_days=lookback_days,
        checkpoint_file=ckpt,
    )
    results: dict[str, list[NoticeData]] = payload["results"]
    filter2_dropped: dict[str, int] = payload["filter2_dropped"]

    for sid in source_ids:
        notices = results.get(sid, [])
        dropped = filter2_dropped.get(sid, 0)
        _print_source_results(sid, notices, dropped)

    _print_summary(results, filter2_dropped)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Validate Philadelphia scraper sources")
    parser.add_argument(
        "--sources",
        nargs="+",
        default=DEFAULT_ORDER,
        metavar="SOURCE_ID",
        help="Source IDs to run (default: all six)",
    )
    parser.add_argument(
        "--lookback",
        type=int,
        default=30,
        metavar="DAYS",
        help="Lookback window in days (default: 30)",
    )
    parser.add_argument(
        "--all",
        action="store_true",
        help="Run all six sources",
    )
    parser.add_argument(
        "--resume-from",
        metavar="CHECKPOINT_FILE",
        help=(
            "Resume fjd_evictions detail-page fetch from a checkpoint file saved "
            "after a network-abort.  Skips the letter sweep and only processes "
            "the un-fetched cases.  Pass the path printed by the previous run."
        ),
    )
    args = parser.parse_args()

    sources_to_run = (
        [s.source_id for s in config.PHILLY_SOURCES]
        if args.all
        else args.sources
    )

    # --resume-from implies fjd_evictions must be in the source list
    resume = getattr(args, "resume_from", None)
    if resume and "fjd_evictions" not in sources_to_run:
        sources_to_run = ["fjd_evictions"]

    asyncio.run(main(sources_to_run, args.lookback, checkpoint_file=resume))
