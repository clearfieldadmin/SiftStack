"""Philadelphia enrichment pipeline — shared by run_philly_micro.py and run_philly_daily.py.

Both entry points call run_pipeline() with their own defaults:

    run_philly_micro.py  →  run_pipeline(sources, lookback, limit=10,   upload=False, slack=False)
    run_philly_daily.py  →  run_pipeline(sources, lookback, limit=None,  upload=True,  slack=True)

All six alignment changes live here exactly once:
  1. Tracerfly scoped to DP candidates (PROBATE_ESTATE only)
  2. Trestle scoped to same DP candidates
  3. Cross-source dedup by parcel_id → address
  4. Smarty RDI commercial filter
  5. Validation gate (drop records missing address / city / zip)
  6. (Bug B fix lives in tracerfy_skip_tracer.py — OPA-aware name split)
"""

from __future__ import annotations

import csv
import datetime
import json
import logging
import re
import time
from pathlib import Path

import config
from datasift_formatter import write_datasift_csv
from enrichment_pipeline import _validate_records
from notice_parser import NoticeData
from philadelphia_scrapers import run_philly_scrape

try:
    from address_standardizer import retry_with_geocoded_city, standardize_addresses
    _SMARTY_AVAILABLE = True
except (ImportError, ModuleNotFoundError) as _e:
    logging.getLogger(__name__).warning(
        "address_standardizer import failed: %s — Smarty step will be skipped", _e
    )
    _SMARTY_AVAILABLE = False
    standardize_addresses = retry_with_geocoded_city = None  # type: ignore

logger = logging.getLogger(__name__)

# Build a stable source_id → notice_type map from config once at import time.
_SOURCE_NOTICE_TYPE: dict[str, str] = {
    s.source_id: s.notice_type for s in config.PHILLY_SOURCES
}

# ── Smarty parcel cache ─────────────────────────────────────────────────────
# Persists Smarty results by OPA parcel_id so bid4assets (full auction list
# re-scraped every run) doesn't burn API credits for already-standardized parcels.

_SMARTY_CACHE_FILE = Path(__file__).resolve().parent.parent / "smarty_parcel_cache.json"
_SMARTY_CACHE_TTL_DAYS = 90   # re-verify with Smarty after 90 days
_SMARTY_CACHE_FIELDS = (
    "address", "city", "state", "zip", "zip_plus4",
    "dpv_match_code", "rdi", "vacant", "latitude", "longitude",
)


def _load_smarty_cache() -> dict:
    if _SMARTY_CACHE_FILE.exists():
        try:
            with open(_SMARTY_CACHE_FILE) as fh:
                return json.load(fh)
        except (json.JSONDecodeError, OSError):
            return {}
    return {}


def _save_smarty_cache(cache: dict) -> None:
    try:
        with open(_SMARTY_CACHE_FILE, "w") as fh:
            json.dump(cache, fh)
    except OSError as exc:
        logger.warning("Failed to save Smarty cache: %s", exc)


def _apply_smarty_cache(
    notices: list[NoticeData], cache: dict
) -> tuple[list[NoticeData], int]:
    """Apply cached Smarty results in-place; return (api_needed, cache_hits).

    Records with a fresh cache entry get their address/geo fields populated
    directly and are excluded from the Smarty API call.  Records with no entry
    or an expired entry (> TTL days old) are returned in api_needed.
    """
    today = datetime.date.today()
    api_needed: list[NoticeData] = []
    hits = 0
    for n in notices:
        pid = (n.parcel_id or "").strip()
        entry = cache.get(pid) if pid else None
        if entry:
            try:
                age = (today - datetime.date.fromisoformat(entry["cached_at"])).days
            except (KeyError, ValueError):
                age = _SMARTY_CACHE_TTL_DAYS + 1
            if age <= _SMARTY_CACHE_TTL_DAYS:
                for field in _SMARTY_CACHE_FIELDS:
                    val = entry.get(field, "")
                    if val:
                        setattr(n, field, val)
                hits += 1
                continue
        api_needed.append(n)
    return api_needed, hits


def _update_smarty_cache(notices: list[NoticeData], cache: dict) -> int:
    """Write successfully standardized records back into cache. Returns count added."""
    today = datetime.date.today().isoformat()
    added = 0
    for n in notices:
        pid = (n.parcel_id or "").strip()
        if pid and n.dpv_match_code:
            cache[pid] = {field: getattr(n, field, "") or "" for field in _SMARTY_CACHE_FIELDS}
            cache[pid]["cached_at"] = today
            added += 1
    return added


# ── Pipeline helpers ────────────────────────────────────────────────────────


def _opa_meta(notice: NoticeData) -> dict:
    try:
        return json.loads(notice.heir_map_json) if notice.heir_map_json else {}
    except (json.JSONDecodeError, TypeError):
        return {}


def _is_dp_candidate(notice: NoticeData) -> bool:
    """True if this notice requires Tracerfly / Trestle deep-prospecting treatment.

    For Philadelphia, only PROBATE_ESTATE records are DP candidates — the decedent
    is deceased by definition and the heir chain is the signing chain.  All other
    sources (li_violations, bid4assets_*, fjd_evictions) use DataSift's bundled
    unlimited skip trace instead.
    """
    return notice.notice_type == "PROBATE_ESTATE"


def _dedup_notices(notices: list[NoticeData]) -> tuple[list[NoticeData], int]:
    """Deduplicate within a single run by OPA parcel_id (primary) then address (secondary).

    A parcel appearing in both li_violations and bid4assets_tax in the same run
    produces one DataSift record, not two.  Source priority is the order records
    are passed in (config.PHILLY_SOURCES order from the caller).

    Returns (deduplicated_list, count_removed).
    """
    seen_parcels: set[str] = set()
    seen_addrs: set[str] = set()
    result: list[NoticeData] = []
    removed = 0
    for n in notices:
        pid  = (n.parcel_id or "").strip()
        addr = (n.address or "").strip().lower()
        if pid:
            if pid in seen_parcels:
                removed += 1
                continue
            seen_parcels.add(pid)
        elif addr:
            if addr in seen_addrs:
                removed += 1
                continue
            seen_addrs.add(addr)
        result.append(n)
    if removed:
        logger.info("Dedup: removed %d cross-source duplicates (%d → %d)",
                    removed, len(notices), len(result))
    return result, removed


def _filter_rdi_commercial(notices: list[NoticeData]) -> tuple[list[NoticeData], int]:
    """Drop records Smarty flagged as RDI='Commercial'.

    Aligned with TN enrichment_pipeline._filter_commercial.  Only fires when rdi
    is explicitly 'Commercial' — empty rdi (Smarty not run or no match) passes through.
    """
    result = [n for n in notices if (n.rdi or "").lower() != "commercial"]
    removed = len(notices) - len(result)
    if removed:
        logger.info("RDI filter: removed %d commercial properties", removed)
    return result, removed


def _count_phones(notice: NoticeData) -> int:
    """Count how many phone fields are populated on a notice."""
    fields = ("primary_phone", "mobile_1", "mobile_2", "mobile_3", "mobile_4",
              "mobile_5", "landline_1", "landline_2", "landline_3")
    return sum(1 for f in fields if getattr(notice, f, ""))


# ── Niche list uploads (Option B-1) ────────────────────────────────────────
# Map Philly notice_type → DataSift niche list name.
# Each daily run uploads once to the "SiftStack {date}" bucket (for skip trace
# targeting), then once per non-empty notice_type to its persistent niche list.
_NICHE_LISTS: dict[str, str] = {
    "CODE_VIOLATION":               "Code Enforcement",
    "SHERIFF_MORTGAGE_FORECLOSURE": "Foreclosure",
    "PROBATE_ESTATE":               "Probate",
    "TAX_SALE":                     "Tax Sale",
    "EVICTION":                     "Eviction",
}


def _write_niche_list_csvs(notices: list[NoticeData]) -> list[dict]:
    """Split records by notice_type and write one DataSift CSV per niche list.

    Only generates CSVs for notice_types that have ≥1 record.  Returns a list
    of dicts: [{path, label, list_name, count}] in _NICHE_LISTS iteration order.
    """
    result: list[dict] = []
    timestamp = time.strftime("%Y%m%d_%H%M%S")
    for notice_type, list_name in _NICHE_LISTS.items():
        subset = [n for n in notices if n.notice_type == notice_type]
        if not subset:
            logger.info("Niche list '%s': 0 records — skipping upload", list_name)
            continue
        safe = list_name.lower().replace(" ", "_")
        path = write_datasift_csv(
            subset,
            filename=f"philly_niche_{safe}_{len(subset)}recs_{timestamp}.csv",
        )
        result.append({"path": path, "label": list_name, "list_name": list_name, "count": len(subset)})
        logger.info("Niche CSV '%s': %d records → %s", list_name, len(subset), path)
    return result


# ── DataSift CSV reader (for --resume-from) ─────────────────────────────────

def _parse_sift_date(s: str) -> str:
    """Convert M/D/YYYY → YYYY-MM-DD. Passes through YYYY-MM-DD and empty strings."""
    if not s:
        return ""
    if re.match(r"^\d{4}-\d{2}-\d{2}$", s):
        return s
    try:
        dt = datetime.datetime.strptime(s.strip(), "%m/%d/%Y")
        return dt.strftime("%Y-%m-%d")
    except ValueError:
        return s


def read_philly_datasift_csv(path: str | Path) -> list[NoticeData]:
    """Read a DataSift-formatted Philadelphia CSV back into NoticeData objects.

    Used by --resume-from to re-run enrichment (Smarty, RDI, validation) and
    re-upload with corrected Lists column without re-scraping or re-tracing.
    Phone and email fields are preserved from the original run.
    """
    notices: list[NoticeData] = []
    with open(path, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            # Reconstruct owner_name: entity records have empty First/Last
            first = r.get("Owner First Name", "").strip()
            last  = r.get("Owner Last Name", "").strip()
            entity_contact = r.get("Entity Contact", "").strip()
            if first or last:
                owner_name = f"{first} {last}".strip()
            elif entity_contact:
                owner_name = entity_contact   # preserves LLC/LP name for entity detection
            else:
                owner_name = ""

            # Auction date: each notice type uses a different built-in column
            nt = r.get("Notice Type", "")
            if "TAX_SALE" in nt or "tax_sale" in nt:
                auction_date = _parse_sift_date(r.get("Tax Auction Date", ""))
            elif "FORECLOSURE" in nt or "foreclosure" in nt:
                auction_date = _parse_sift_date(r.get("Foreclosure Date", ""))
            elif "PROBATE" in nt or "probate" in nt:
                auction_date = _parse_sift_date(r.get("Probate Open Date", ""))
            else:
                auction_date = ""

            n = NoticeData(
                notice_type   = nt,
                address       = r.get("Property Street Address", ""),
                city          = r.get("Property City", ""),
                state         = r.get("Property State", ""),
                zip           = r.get("Property ZIP Code", ""),
                owner_name    = owner_name,
                owner_street  = r.get("Mailing Street Address", ""),
                owner_city    = r.get("Mailing City", ""),
                owner_state   = r.get("Mailing State", ""),
                owner_zip     = r.get("Mailing ZIP Code", ""),
                parcel_id     = r.get("Parcel ID", ""),
                county        = r.get("County", ""),
                date_added    = _parse_sift_date(r.get("Date Added", "")),
                auction_date  = auction_date,
                source_url    = r.get("Source URL", ""),
                # Phones preserved from original run
                primary_phone = r.get("Phone 1", ""),
                mobile_1      = r.get("Phone 2", ""),
                mobile_2      = r.get("Phone 3", ""),
                mobile_3      = r.get("Phone 4", ""),
                mobile_4      = r.get("Phone 5", ""),
                mobile_5      = r.get("Phone 6", ""),
                landline_1    = r.get("Phone 7", ""),
                landline_2    = r.get("Phone 8", ""),
                landline_3    = r.get("Phone 9", ""),
                email_1       = r.get("Email 1", ""),
                email_2       = r.get("Email 2", ""),
                email_3       = r.get("Email 3", ""),
                email_4       = r.get("Email 4", ""),
                email_5       = r.get("Email 5", ""),
                # Property enrichment
                estimated_value     = r.get("Estimated Value", ""),
                mls_status          = r.get("MSL Status", ""),
                mls_last_sold_date  = _parse_sift_date(r.get("Last Sale Date", "")),
                mls_last_sold_price = r.get("Last Sale Price", ""),
                equity_percent      = r.get("Equity Percentage", ""),
                tax_delinquent_amount = r.get("Tax Deliquent Value", ""),  # DataSift typo
                tax_delinquent_years  = r.get("Tax Delinquent Year", ""),
                year_built  = r.get("Year Built", ""),
                sqft        = r.get("Living SqFt", ""),
                bedrooms    = r.get("Bedrooms", ""),
                bathrooms   = r.get("Bathrooms", ""),
                # Deep prospecting
                owner_deceased          = r.get("Owner Deceased", ""),
                date_of_death           = r.get("Date of Death", ""),
                decedent_name           = r.get("Decedent Name", ""),
                decision_maker_name     = r.get("Decision Maker", ""),
                decision_maker_relationship = r.get("DM Relationship", ""),
                dm_confidence           = r.get("DM Confidence", ""),
                obituary_url            = r.get("Obituary URL", ""),
                # Entity
                entity_type        = r.get("Entity Type", ""),
                entity_person_name = r.get("Entity Contact", ""),
            )
            notices.append(n)
    logger.info("Resume: read %d records from %s", len(notices), path)
    return notices


# ── Main entry point ────────────────────────────────────────────────────────


async def run_pipeline(
    sources: list[str],
    lookback: int = 30,
    limit: int | None = None,
    upload_datasift: bool = False,
    notify_slack: bool = False,
    filename: str | None = None,
    resume_from: str | None = None,
    phone_scoring: bool = True,
) -> dict:
    """Run the full Philadelphia enrichment pipeline.

    Args:
        sources:          List of enabled source_ids to scrape.
        lookback:         Days to look back for each source.
        limit:            Max records per source (None = no cap, i.e. production).
        upload_datasift:  Upload CSV to DataSift via Playwright after generation.
        notify_slack:     Send run summary to Slack/Discord webhook.
        filename:         Optional CSV filename override.

    Returns a dict with:
        csv_path           Path to the written DataSift CSV
        records            list[NoticeData] — final records (post all filters)
        records_by_source  dict[source_id → list] — post-cap, pre-filter (for display)
        stats              dict — per-step counts and costs
        upload_result      dict | None
    """
    t_start = time.time()

    stats: dict = {
        "scraped_by_source":    {},
        "opa_matched_by_source": {},
        "f2_dropped_by_source": {},
        "capped_by_source":     {},
        "total_after_cap":      0,
        "dedup_removed":        0,
        "total_after_dedup":    0,
        "smarty_matched":       0,
        "smarty_skipped":       0,
        "smarty_cache_hits":    0,
        "smarty_api_calls":     0,
        "smarty_cache_added":   0,
        "rdi_removed":          0,
        "validation_removed":   0,
        "dp_candidates":        0,
        "tracerfy":             {},
        "tracerfy_cost":        0.0,
        "trestle_scored":       0,
        "trestle_cost":         0.0,
        "probate_dropped":      0,
        "csv_written":          0,
        "total_cost":           0.0,
        "elapsed_s":            0.0,
    }

    # ── 1–3. Scrape / Cap / Dedup  (or resume from existing CSV) ────────────
    records_by_source: dict[str, list[NoticeData]] = {}

    if resume_from:
        # --resume-from: skip scrape, cap, and dedup — read records directly from
        # an existing DataSift CSV.  Smarty / RDI / validation still run so the
        # new CSV gets standardized addresses and the fixed Lists column.
        # Tracerfly and Trestle are also skipped — phones are already in the CSV.
        logger.info("── Resume mode: reading records from %s ──", resume_from)
        notices = read_philly_datasift_csv(resume_from)
        stats["total_after_cap"]   = len(notices)
        stats["total_after_dedup"] = len(notices)
        for sid in sources:
            records_by_source[sid] = []   # empty — not available in resume mode
        logger.info("Resume: %d records loaded (scrape/dedup skipped)", len(notices))
    else:
        # Normal path: scrape all sources
        logger.info("── Step 1: Scrape ──")
        payload = await run_philly_scrape(
            source_ids=sources,
            lookback_days=lookback,
            evictions_max_detail=limit,
        )
        raw_results: dict[str, list[NoticeData]] = payload["results"]
        f2_dropped:  dict[str, int]              = payload["filter2_dropped"]

        for sid in sources:
            all_recs = raw_results.get(sid, [])
            stats["scraped_by_source"][sid]    = len(all_recs)
            stats["f2_dropped_by_source"][sid] = f2_dropped.get(sid, 0)
            stats["opa_matched_by_source"][sid] = sum(
                1 for n in all_recs if _opa_meta(n).get("opa_match") is True
            )

        # ── 2. Cap per source ────────────────────────────────────────────────
        notices: list[NoticeData] = []
        for sid in sources:
            recs = raw_results.get(sid, [])
            capped = recs[:limit] if limit is not None else recs
            records_by_source[sid] = capped
            stats["capped_by_source"][sid] = len(capped)
            notices.extend(capped)
        stats["total_after_cap"] = len(notices)
        logger.info("After cap: %d total records", len(notices))

        # ── 3. Dedup by parcel_id → address ─────────────────────────────────
        logger.info("── Step 3: Dedup ──")
        notices, removed = _dedup_notices(notices)
        stats["dedup_removed"]   = removed
        stats["total_after_dedup"] = len(notices)

    # ── 4. Smarty address standardization (cache-aware) ─────────────────────
    logger.info("── Step 4: Smarty ──")
    if _SMARTY_AVAILABLE and config.SMARTY_AUTH_ID and config.SMARTY_AUTH_TOKEN:
        smarty_cache = _load_smarty_cache()
        api_needed, cache_hits = _apply_smarty_cache(notices, smarty_cache)
        stats["smarty_cache_hits"] = cache_hits

        if api_needed:
            standardize_addresses(api_needed, config.SMARTY_AUTH_ID, config.SMARTY_AUTH_TOKEN)
            retry_with_geocoded_city(api_needed, config.SMARTY_AUTH_ID, config.SMARTY_AUTH_TOKEN)
            stats["smarty_cache_added"] = _update_smarty_cache(api_needed, smarty_cache)
            _save_smarty_cache(smarty_cache)

        stats["smarty_api_calls"] = len(api_needed)
        stats["smarty_matched"]   = sum(1 for n in notices if n.dpv_match_code)
        stats["smarty_skipped"]   = sum(1 for n in notices if not n.address.strip())
        logger.info(
            "Smarty: %d API calls, %d cache hits, %d newly cached, %d total matched",
            stats["smarty_api_calls"], cache_hits,
            stats["smarty_cache_added"], stats["smarty_matched"],
        )
    else:
        reason = "SDK unavailable" if not _SMARTY_AVAILABLE else "credentials not set"
        logger.info("Smarty skipped (%s)", reason)

    # ── 5. RDI commercial filter ─────────────────────────────────────────────
    logger.info("── Step 5: RDI commercial filter ──")
    notices, stats["rdi_removed"] = _filter_rdi_commercial(notices)

    # ── 6. Validation gate ───────────────────────────────────────────────────
    logger.info("── Step 6: Validation gate ──")
    before_validation = len(notices)
    notices = _validate_records(notices)
    stats["validation_removed"] = before_validation - len(notices)
    logger.info("Validation: %d removed, %d remaining",
                stats["validation_removed"], len(notices))

    if not notices:
        logger.warning("No records remaining after filters — aborting pipeline")
        stats["elapsed_s"] = time.time() - t_start
        return {
            "csv_path": None,
            "records": [],
            "records_by_source": records_by_source,
            "stats": stats,
            "upload_result": None,
        }

    # ── 7. Tracerfly — DP candidates only (PROBATE_ESTATE) ──────────────────
    # Skipped in resume mode — phones already preserved from the original run.
    logger.info("── Step 7: Tracerfly (DP candidates only) ──")
    dp_candidates = [n for n in notices if _is_dp_candidate(n)]
    non_dp = len(notices) - len(dp_candidates)
    stats["dp_candidates"] = len(dp_candidates)

    if resume_from:
        logger.info("Tracerfly: skipped (resume mode — phones preserved from source CSV)")
    elif dp_candidates:
        logger.info("Tracerfly: %d DP candidates (%d non-DP skipped → DataSift bundled skip trace)",
                    len(dp_candidates), non_dp)
        if config.TRACERFY_API_KEY:
            from tracerfy_skip_tracer import batch_skip_trace
            tracerfy_stats = batch_skip_trace(
                dp_candidates,
                max_signing_traces=5,
                lookup_heir_addresses=False,
            )
            stats["tracerfy"]      = tracerfy_stats
            stats["tracerfy_cost"] = float(tracerfy_stats.get("cost", 0))
            logger.info("Tracerfly: %d/%d matched, %d phones, %d emails, $%.4f",
                        tracerfy_stats.get("matched", 0), tracerfy_stats.get("submitted", 0),
                        tracerfy_stats.get("phones_found", 0), tracerfy_stats.get("emails_found", 0),
                        stats["tracerfy_cost"])
        else:
            logger.info("Tracerfly: TRACERFY_API_KEY not set — skipping")
    else:
        logger.info("Tracerfly: 0 DP candidates — skipped")

    # ── 8. Trestle phone scoring — DP candidates only ────────────────────────
    # Skipped in resume mode — Trestle scores already preserved from source CSV.
    logger.info("── Step 8: Trestle phone scoring (DP candidates only) ──")
    if resume_from:
        logger.info("Trestle: skipped (resume mode — scores preserved from source CSV)")
    elif dp_candidates and config.TRESTLE_API_KEY:
        from phone_validator import score_record_phones
        phone_results = score_record_phones(
            dp_candidates,
            api_key=config.TRESTLE_API_KEY,
            add_litigator=False,
        )
        stats["trestle_scored"] = len(phone_results)
        stats["trestle_cost"]   = stats["trestle_scored"] * 0.01
        logger.info("Trestle: %d phones scored, est. $%.4f",
                    stats["trestle_scored"], stats["trestle_cost"])
    elif dp_candidates:
        logger.info("Trestle: TRESTLE_API_KEY not set — skipping")
    else:
        logger.info("Trestle: no DP candidates — skipped")

    # ── 9. DataSift CSV generation ───────────────────────────────────────────
    logger.info("── Step 9: DataSift CSV ──")

    # Pre-compute probate_dropped to match what write_datasift_csv will drop (Bug 3).
    stats["probate_dropped"] = sum(
        1 for n in notices
        if n.notice_type == "PROBATE_ESTATE"
        and not _opa_meta(n).get("opa_match")
        and not n.address
    )

    if filename is None:
        if resume_from:
            tag = "resume"
        elif limit is not None:
            tag = "micro"
        else:
            tag = "daily"
        filename = f"philly_{tag}_{len(notices)}recs_{time.strftime('%Y%m%d_%H%M%S')}.csv"

    csv_path = write_datasift_csv(notices, filename=filename)
    stats["csv_written"] = len(notices) - stats["probate_dropped"]
    logger.info("CSV written: %s  (%d records, %d probate dropped)",
                csv_path, stats["csv_written"], stats["probate_dropped"])

    # ── 10. DataSift upload ──────────────────────────────────────────────────
    upload_result: dict | None = None
    if upload_datasift:
        logger.info("── Step 10: DataSift upload ──")
        try:
            from datasift_formatter import write_datasift_split_csvs
            from datasift_uploader import upload_datasift_split, upload_to_datasift

            csv_infos = write_datasift_split_csvs(notices)
            for info in csv_infos:
                logger.info("DataSift CSV (%s): %s", info["label"], info["path"])

            if len(csv_infos) > 1:
                upload_result = await upload_datasift_split(
                    csv_infos, enrich=True, skip_trace=True,
                )
            else:
                upload_result = await upload_to_datasift(
                    csv_infos[0]["path"], enrich=True, skip_trace=True,
                )

            if upload_result and upload_result.get("success"):
                logger.info("DataSift upload: %s", upload_result.get("message", "OK"))
            else:
                logger.error("DataSift upload failed: %s",
                             upload_result.get("message") if upload_result else "no result")
        except Exception as exc:
            logger.error("DataSift upload error: %s", exc, exc_info=True)
            upload_result = {"success": False, "message": str(exc)}

    # ── 10b. Niche list uploads (Option B-1) ────────────────────────────────
    # Each niche list upload runs in its OWN browser session to avoid shared
    # page-state corruption (open dropdowns, stale wizard steps) that caused
    # all 5 uploads to fail when sharing one session.
    # Enrich and skip trace are NOT triggered here — bucket only.
    niche_results: list[dict] = []
    if upload_datasift:
        niche_csvs = _write_niche_list_csvs(notices)
        if niche_csvs:
            logger.info("── Step 10b: Niche list uploads (%d lists) ──", len(niche_csvs))
            from playwright.async_api import async_playwright
            from datasift_core import login as _ds_login
            from datasift_uploader import upload_csv as _upload_csv

            _UA = (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/120.0.0.0 Safari/537.36"
            )

            for _info in niche_csvs:
                try:
                    async with async_playwright() as _pw:
                        _browser = await _pw.chromium.launch(headless=True)
                        _ctx = await _browser.new_context(
                            viewport={"width": 1280, "height": 720},
                            user_agent=_UA,
                        )
                        _page = await _ctx.new_page()
                        _ok = await _ds_login(_page, config.DATASIFT_EMAIL, config.DATASIFT_PASSWORD)
                        if _ok:
                            # Try adding to existing niche list.
                            # Fall back to creating new on first-ever run.
                            _r = await _upload_csv(
                                _page, _info["path"],
                                list_name=_info["list_name"],
                                existing_list=True,
                            )
                            if not _r.get("success"):
                                logger.info("  '%s': existing list not found — creating new",
                                            _info["list_name"])
                                _r = await _upload_csv(
                                    _page, _info["path"],
                                    list_name=_info["list_name"],
                                    existing_list=False,
                                )
                        else:
                            _r = {"success": False, "message": "DataSift login failed"}
                        await _browser.close()
                except Exception as _exc:
                    _r = {"success": False, "message": str(_exc)}
                    logger.error("Niche upload '%s' error: %s", _info["list_name"], _exc)

                _status = "OK" if _r.get("success") else f"FAILED: {_r.get('message', '')}"
                logger.info("Niche upload '%s' (%d records): %s",
                            _info["list_name"], _info["count"], _status)
                niche_results.append({
                    **_info,
                    "success": _r.get("success", False),
                    "message": _r.get("message", ""),
                })

    # ── 11. Phone scoring (wait for skip trace → Trestle → upload tags) ────────
    # Runs only when: upload happened AND phone_scoring=True AND not micro-run.
    # Polls the daily bucket for phones (up to 3 × 5 min), then scores with
    # Trestle and uploads tier tags back.  Failures are non-fatal — pipeline
    # continues and reports the failure in the Slack summary.
    phone_result: dict = {}
    if upload_datasift and phone_scoring and limit is None:
        logger.info("── Step 11: Phone scoring (wait for skip trace) ──")
        try:
            from phone_scorer import score_and_tag
            bucket_list = f"SiftStack {datetime.date.today().isoformat()}"
            phone_result = await score_and_tag(
                list_name=bucket_list,
                email=config.DATASIFT_EMAIL,
                password=config.DATASIFT_PASSWORD,
                api_key=config.TRESTLE_API_KEY or "",
                do_upload=True,
                max_retries=3,
                wait_seconds=300,
            )
            trestle_cost_phone = phone_result.get("cost", 0.0)
            stats["trestle_cost"]  = stats.get("trestle_cost", 0.0) + trestle_cost_phone
            logger.info(
                "Phone scoring: %d found, %d scored, $%.2f  (upload: %s)",
                phone_result.get("phones_found", 0),
                phone_result.get("phones_scored", 0),
                trestle_cost_phone,
                "OK" if phone_result.get("upload_ok") else "FAIL",
            )
        except Exception as exc:
            logger.error("Phone scoring failed: %s", exc, exc_info=True)
            phone_result = {"skipped": True, "message": str(exc)}
    elif upload_datasift and phone_scoring and limit is not None:
        logger.info("Phone scoring skipped (micro-run — limit set)")
    elif upload_datasift and not phone_scoring:
        logger.info("Phone scoring skipped (--no-phone-scoring)")

    # ── Totals ───────────────────────────────────────────────────────────────
    stats["total_cost"] = stats["tracerfy_cost"] + stats.get("trestle_cost", 0.0)
    stats["elapsed_s"]  = time.time() - t_start

    # ── 12. Combined Slack summary ────────────────────────────────────────────
    if notify_slack and config.SLACK_WEBHOOK_URL:
        try:
            from slack_notifier import _send_webhook
            _send_webhook(_build_slack_summary(
                sources=sources,
                stats=stats,
                notices=notices,
                upload_result=upload_result,
                niche_results=niche_results,
                phone_result=phone_result,
                resume_from=resume_from,
            ))
            logger.info("Slack notification sent")
        except Exception as exc:
            logger.warning("Slack notification failed: %s", exc)

    return {
        "csv_path":          csv_path,
        "records":           notices,
        "records_by_source": records_by_source,
        "stats":             stats,
        "upload_result":     upload_result,
        "niche_results":     niche_results,
        "phone_result":      phone_result,
    }


def _build_slack_summary(
    sources: list[str],
    stats: dict,
    notices: list,
    upload_result: dict | None,
    niche_results: list[dict],
    phone_result: dict,
    resume_from: str | None,
) -> str:
    """Build a single combined Slack message for the full daily run."""
    lines: list[str] = []
    mode = "Resume" if resume_from else "Daily"
    lines.append(f"*SiftStack Philly — {mode} Run Complete*")

    # Scrape counts
    if not resume_from:
        src_parts = []
        for sid in sources:
            n = stats["scraped_by_source"].get(sid, 0)
            src_parts.append(f"{sid.replace('_', ' ')}={n}")
        lines.append(f"Scrape: {' | '.join(src_parts)}")
    else:
        lines.append(f"Source CSV: {resume_from}")

    # Filters
    lines.append(
        f"Filters: dedup −{stats['dedup_removed']}  "
        f"RDI −{stats['rdi_removed']}  "
        f"validation −{stats['validation_removed']}  "
        f"→ {stats['csv_written']} records"
    )

    # Bucket upload
    ur = upload_result or {}
    bucket_ok = "✓" if ur.get("success") else "✗"
    lines.append(
        f"Bucket: SiftStack {__import__('datetime').date.today()} — "
        f"{bucket_ok} ({stats['csv_written']} records)"
    )

    # Niche uploads
    if niche_results:
        niche_line = "  ".join(
            f"{'✓' if r['success'] else '✗'} {r['list_name']} ({r['count']})"
            for r in niche_results
        )
        lines.append(f"Niche: {niche_line}")
    else:
        lines.append("Niche: not run")

    # Phone scoring
    if phone_result:
        if phone_result.get("skipped"):
            lines.append(f"Phones: skipped — {phone_result.get('message','')}")
        else:
            tier_parts = "  ".join(
                f"{t}: {c}" for t, c in phone_result.get("tier_counts", {}).items() if c
            )
            tag_ok = "✓" if phone_result.get("upload_ok") else "✗"
            lines.append(
                f"Phones: {phone_result.get('phones_found', 0)} found → "
                f"{phone_result.get('phones_scored', 0)} scored  "
                f"tags {tag_ok}  |  {tier_parts}"
            )
    else:
        lines.append("Phones: not run")

    # Cost + elapsed
    lines.append(
        f"Cost: Tracerfly ${stats['tracerfy_cost']:.2f} + "
        f"Trestle ${stats.get('trestle_cost', 0.0):.2f} = "
        f"${stats['total_cost']:.2f}"
    )
    elapsed_min = stats["elapsed_s"] / 60
    lines.append(f"Elapsed: {elapsed_min:.0f} min")

    return "\n".join(lines)
