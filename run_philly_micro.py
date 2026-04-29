"""Dry-run micro-sample pipeline for Philadelphia sources.

Takes the first N records from each enabled source, runs the full production
enrichment chain via philly_pipeline.run_pipeline(), and prints a detailed
report.  Does NOT upload to DataSift or send Slack.

Identical pipeline logic to run_philly_daily.py — the only difference is
limit=10 and upload/slack both False.  This validates that production logic
works correctly before committing to a live run.

Usage:
    python run_philly_micro.py
    python run_philly_micro.py --lookback 30 --limit 10
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from dataclasses import asdict

sys.path.insert(0, "src")

import config
from datasift_formatter import DATASIFT_COLUMNS, _build_row
from notice_parser import NoticeData
from philly_pipeline import run_pipeline

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("philly_micro")

for _noisy in ("httpx", "httpcore", "asyncio", "urllib3"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

ENABLED_SOURCES = [s.source_id for s in config.PHILLY_SOURCES if s.enabled]

# source_id → notice_type (for per-source display lookups)
_SOURCE_NT = {s.source_id: s.notice_type for s in config.PHILLY_SOURCES}


# ── Display helpers ────────────────────────────────────────────────────────────

def _truncate(v: object, n: int = 80) -> str:
    s = str(v) if v is not None else ""
    return s if len(s) <= n else s[: n - 1] + "…"


def _meta(n: NoticeData) -> dict:
    try:
        return json.loads(n.heir_map_json) if n.heir_map_json else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _phones_from_notice(n: NoticeData) -> list[str]:
    phones = []
    for f in ("primary_phone", "mobile_1", "mobile_2", "mobile_3", "mobile_4",
              "mobile_5", "landline_1", "landline_2", "landline_3"):
        v = getattr(n, f, "")
        if v:
            phones.append(v)
    return phones


def _print_record_full(idx: int, source_id: str, n: NoticeData, csv_row: dict) -> None:
    print(f"\n  ── [{source_id}] Record {idx} ──────────────────────────────")
    d = asdict(n)
    print("  NOTICE DATA FIELDS:")
    for k, v in d.items():
        if v not in (None, "", [], {}):
            print(f"    {k:<30} {_truncate(v)}")
    print("  DATASIFT CSV COLUMNS:")
    for col in DATASIFT_COLUMNS:
        v = csv_row.get(col, "")
        if v:
            print(f"    {col:<35} {_truncate(v)}")


def _print_banner(title: str) -> None:
    print(f"\n{'=' * 70}")
    print(f"  {title}")
    print("=" * 70)


# ── Entry point ────────────────────────────────────────────────────────────────

async def main(lookback_days: int, limit: int) -> None:
    print(f"\nPhiladelphia Micro-Run  (limit={limit}/source, lookback={lookback_days}d)")
    print(f"Sources: {', '.join(ENABLED_SOURCES)}\n")

    result = await run_pipeline(
        sources=ENABLED_SOURCES,
        lookback=lookback_days,
        limit=limit,
        upload_datasift=False,
        notify_slack=False,
    )

    csv_path          = result["csv_path"]
    records: list[NoticeData] = result["records"]
    by_source         = result["records_by_source"]  # post-cap, pre-filter
    st                = result["stats"]

    # Per-source Tracerfly phone count (from final records, post-filter)
    nt_to_sid = {v: k for k, v in _SOURCE_NT.items()}
    tracerfy_counts = {
        sid: sum(1 for n in records
                 if n.notice_type == _SOURCE_NT.get(sid) and _phones_from_notice(n))
        for sid in ENABLED_SOURCES
    }

    # Per-source Smarty count (dpv_match_code set)
    smarty_counts = {
        sid: sum(1 for n in records
                 if n.notice_type == _SOURCE_NT.get(sid) and n.dpv_match_code)
        for sid in ENABLED_SOURCES
    }

    # ── Scrape banner (echoes pipeline log in structured form) ────────────────
    _print_banner("SCRAPE + OPA enrichment")
    for sid in ENABLED_SOURCES:
        scraped = st["scraped_by_source"].get(sid, 0)
        capped  = st["capped_by_source"].get(sid, 0)
        opa     = st["opa_matched_by_source"].get(sid, 0)
        f2d     = st["f2_dropped_by_source"].get(sid, 0)
        print(
            f"  {sid:<25} scraped={scraped:>4}  "
            f"capped={capped:>2}  "
            f"opa_match={opa}/{capped}  "
            f"f2_dropped={f2d}"
        )
    print(f"\n  Total after cap  : {st['total_after_cap']}")
    print(f"  Dedup removed    : {st['dedup_removed']}")
    print(f"  RDI removed      : {st['rdi_removed']}")
    print(f"  Validation removed: {st['validation_removed']}")
    print(f"  Total final      : {len(records)}")

    # ── Smarty ────────────────────────────────────────────────────────────────
    _print_banner("SMARTY address standardization")
    if config.SMARTY_AUTH_ID:
        for sid in ENABLED_SOURCES:
            cap = st["capped_by_source"].get(sid, 0)
            print(f"  {sid:<25} smarty_standardized={smarty_counts[sid]}/{cap}")
    else:
        print("  Smarty skipped (credentials not set)")

    # ── Tracerfly ─────────────────────────────────────────────────────────────
    _print_banner("TRACERFLY skip tracing  [DP candidates = PROBATE_ESTATE only]")
    tr = st.get("tracerfy", {})
    dp = st["dp_candidates"]
    total_cap = st["total_after_cap"]
    print(f"  DP candidates submitted : {dp}/{total_cap} (non-DP → DataSift bundled skip trace)")
    if tr:
        print(
            f"  Tracerfy stats: submitted={tr.get('submitted', 0)}  "
            f"matched={tr.get('matched', 0)}  "
            f"phones={tr.get('phones_found', 0)}  "
            f"emails={tr.get('emails_found', 0)}  "
            f"cost=${st['tracerfy_cost']:.4f}"
        )
    for sid in ENABLED_SOURCES:
        nt = _SOURCE_NT.get(sid, "")
        cap = st["capped_by_source"].get(sid, 0)
        is_dp = (nt == "PROBATE_ESTATE")
        tag   = "" if is_dp else "  [non-DP — DataSift]"
        print(f"  {sid:<25} phones_found={tracerfy_counts[sid]}/{cap}{tag}")

    # ── Trestle ───────────────────────────────────────────────────────────────
    _print_banner("TRESTLE phone scoring  [DP candidates only]")
    print(f"  Phones scored: {st['trestle_scored']}  est_cost=${st['trestle_cost']:.4f}"
          + ("  (SKIPPED — no API key)" if not config.TRESTLE_API_KEY else ""))

    # ── CSV ───────────────────────────────────────────────────────────────────
    _print_banner("DATASIFT CSV generation")
    print(f"\n  CSV written: {csv_path}")
    print(f"  Records written: {st['csv_written']}  (probate dropped: {st['probate_dropped']})")
    print(f"  Columns: {len(DATASIFT_COLUMNS)}")

    # Build CSV rows for the display sections
    csv_rows: dict[int, dict] = {id(n): _build_row(n) for n in records}

    # ── A. CSV file path ──────────────────────────────────────────────────────
    _print_banner("A. CSV FILE PATH")
    print(f"  {csv_path}")

    # ── B. All records — full field dump ──────────────────────────────────────
    _print_banner(f"B. ALL {len(records)} RECORDS — FULL FIELD DUMP")
    for sid in ENABLED_SOURCES:
        nt = _SOURCE_NT.get(sid, "")
        sid_recs = [n for n in records if n.notice_type == nt]
        print(f"\n  ┌─ SOURCE: {sid}  ({len(sid_recs)} records) ─────────────────")
        for i, n in enumerate(sid_recs, 1):
            _print_record_full(i, sid, n, csv_rows.get(id(n), {}))

    # ── C. Per-step success counts ────────────────────────────────────────────
    _print_banner("C. ENRICHMENT SUCCESS COUNTS")
    print(f"  {'Source':<25} {'OPA':>6} {'Smarty':>7} {'Tracerfy':>9}")
    print(f"  {'-'*25} {'-'*6} {'-'*7} {'-'*9}")
    for sid in ENABLED_SOURCES:
        cap   = st["capped_by_source"].get(sid, 0)
        denom = f"/{cap}"
        opa_s = f"{st['opa_matched_by_source'].get(sid, 0)}{denom}"
        smt_s = f"{smarty_counts[sid]}{denom}" if config.SMARTY_AUTH_ID else "SKIP"
        nt    = _SOURCE_NT.get(sid, "")
        if nt == "PROBATE_ESTATE":
            trc_s = f"{tracerfy_counts[sid]}{denom}" if config.TRACERFY_API_KEY else "SKIP"
        else:
            trc_s = "DataSift"
        print(f"  {sid:<25} {opa_s:>6} {smt_s:>7} {trc_s:>9}")
    print(f"\n  Trestle phones scored: {st['trestle_scored']}"
          + ("  (SKIPPED)" if not config.TRESTLE_API_KEY else ""))

    # ── D. Total cost ─────────────────────────────────────────────────────────
    _print_banner(f"D. ENRICHMENT COST (micro-run {st['total_after_cap']} records)")
    print(f"  Tracerfy skip trace : ${st['tracerfy_cost']:.4f}"
          f"  [{dp} DP candidates @ $0.02]")
    print(f"  Trestle phone score : ${st['trestle_cost']:.4f}"
          f"  (est. $0.01/phone)")
    print(f"  Smarty address      : $0.0000  (bulk plan — no per-call charge)")
    print(f"  OPA (Carto)         : $0.0000  (public API)")
    print(f"  {'─'*36}")
    print(f"  TOTAL               : ${st['total_cost']:.4f}")

    # ── E. Issues ────────────────────────────────────────────────────────────
    _print_banner("E. ISSUES")
    issues_found = False
    for n in records:
        if not n.address:
            sid = nt_to_sid.get(n.notice_type, n.notice_type)
            print(f"  [{sid}] Missing address: {n.owner_name or n.decedent_name}")
            issues_found = True
        if not n.zip:
            sid = nt_to_sid.get(n.notice_type, n.notice_type)
            print(f"  [{sid}] Missing ZIP: {n.address}")
            issues_found = True
    if not issues_found:
        print("  None detected.")

    print(f"\n  Run time: {st['elapsed_s']:.0f}s\n")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Philadelphia dry-run micro-sample pipeline")
    parser.add_argument("--lookback", type=int, default=30, metavar="DAYS")
    parser.add_argument("--limit",    type=int, default=10, metavar="N",
                        help="Max records per source (default: 10)")
    args = parser.parse_args()
    asyncio.run(main(args.lookback, args.limit))
