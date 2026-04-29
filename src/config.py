"""Configuration for SiftStack — full-stack REI operations platform."""

import json
import logging
import os
import re
from dataclasses import dataclass
from pathlib import Path
from dotenv import load_dotenv

load_dotenv()

# ── Paths ──────────────────────────────────────────────────────────────
PROJECT_ROOT = Path(__file__).resolve().parent.parent
OUTPUT_DIR = PROJECT_ROOT / "output"
LOG_DIR = PROJECT_ROOT / "logs"
STATE_FILE = PROJECT_ROOT / "last_run.json"
SEEN_IDS_FILE = PROJECT_ROOT / "seen_ids.json"
SEEN_IDS_PRUNE_DAYS = 90
# Notices that exhausted all CAPTCHA retries during scraping.
# Persisted so the next run's summary can surface them instead of
# silently dropping — and a future retry pass can prioritize them.
CAPTCHA_FAILED_IDS_FILE = PROJECT_ROOT / "captcha_failed_ids.json"
CAPTCHA_FAILED_PRUNE_DAYS = 14
COOKIES_FILE = PROJECT_ROOT / "cookies.json"
DROPBOX_STATE_FILE = PROJECT_ROOT / "dropbox_state.json"
PHOTO_STATE_FILE = PROJECT_ROOT / "photo_state.json"

# ── Dropbox Watcher ────────────────────────────────────────────────────
_v = os.getenv("DROPBOX_POLL_INTERVAL", "").strip()
DROPBOX_POLL_INTERVAL = int(_v) if _v else 900  # seconds (default 15 min)
DROPBOX_ROOT_FOLDER = os.getenv("DROPBOX_ROOT_FOLDER", "")  # root folder path in Dropbox, e.g. "/TN Public Notice"
DROPBOX_STORAGE_WARN_PERCENT = 80  # warn when storage usage exceeds this %

OUTPUT_DIR.mkdir(exist_ok=True)
LOG_DIR.mkdir(exist_ok=True)

# ── Credentials ────────────────────────────────────────────────────────
TNPN_EMAIL = os.getenv("TNPN_EMAIL", "")
TNPN_PASSWORD = os.getenv("TNPN_PASSWORD", "")
CAPTCHA_API_KEY = os.getenv("CAPTCHA_API_KEY", "")      # 2Captcha API key (legacy TN scraper)
CAPSOLVER_API_KEY = os.getenv("CAPSOLVER_API_KEY", "") # CapSolver — FJD reCAPTCHA v3
SCRAPFLY_API_KEY = os.getenv("SCRAPFLY_API_KEY", "")   # Scrapfly — Bid4Assets ASP bypass
ANTHROPIC_API_KEY = os.getenv("ANTHROPIC_API_KEY", "")  # Claude Haiku for LLM parsing
SMARTY_AUTH_ID = os.getenv("SMARTY_AUTH_ID", "")        # Smarty address standardization
SMARTY_AUTH_TOKEN = os.getenv("SMARTY_AUTH_TOKEN", "")
OPENWEBNINJA_API_KEY = os.getenv("OPENWEBNINJA_API_KEY", "")  # Zillow property enrichment
SERPER_API_KEY = os.getenv("SERPER_API_KEY", "")              # Serper.dev Google Search API
FIRECRAWL_API_KEY = os.getenv("FIRECRAWL_API_KEY", "")        # Firecrawl JS-rendered scraping
TRACERFY_API_KEY = os.getenv("TRACERFY_API_KEY", "")          # Tracerfy skip tracing
TRESTLE_API_KEY = os.getenv("TRESTLE_API_KEY", "")            # Trestle phone validation
DATASIFT_EMAIL = os.getenv("DATASIFT_EMAIL", "")              # DataSift.ai login
DATASIFT_PASSWORD = os.getenv("DATASIFT_PASSWORD", "")
SLACK_WEBHOOK_URL = os.getenv("SLACK_WEBHOOK_URL", "")        # Slack/Discord webhook
ANCESTRY_EMAIL = os.getenv("ANCESTRY_EMAIL", "")              # Ancestry.com login
ANCESTRY_PASSWORD = os.getenv("ANCESTRY_PASSWORD", "")
DROPBOX_APP_KEY = os.getenv("DROPBOX_APP_KEY", "")            # Dropbox OAuth2 app key
DROPBOX_APP_SECRET = os.getenv("DROPBOX_APP_SECRET", "")
DROPBOX_REFRESH_TOKEN = os.getenv("DROPBOX_REFRESH_TOKEN", "")

# ── LLM Backend ──────────────────────────────────────────────────────
LLM_BACKEND = os.getenv("LLM_BACKEND", "anthropic")           # "anthropic", "ollama", or "openrouter"
LLM_MODEL = os.getenv("LLM_MODEL", "claude-haiku-4-5-20251001")  # Anthropic model name
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "qwen2.5:7b")        # Local Ollama model
OLLAMA_BASE_URL = os.getenv("OLLAMA_BASE_URL", "http://localhost:11434/v1/")
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")       # OpenRouter API key
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "qwen/qwen-2.5-72b-instruct")
OPENROUTER_BASE_URL = os.getenv("OPENROUTER_BASE_URL", "https://openrouter.ai/api/v1")

# ── Site URLs ──────────────────────────────────────────────────────────
BASE_URL = "https://www.tnpublicnotice.com"
LOGIN_URL = f"{BASE_URL}/authenticate.aspx"
SMART_SEARCH_URL = f"{BASE_URL}/Smartsearch/Default.aspx"

# ── ASP.NET Selectors ─────────────────────────────────────────────────
# Login form
SEL_LOGIN_EMAIL = "#ctl00_ContentPlaceHolder1_AuthenticateIPA1_txtEmailAddress"
SEL_LOGIN_PASSWORD = "#ctl00_ContentPlaceHolder1_AuthenticateIPA1_txtPassword"
SEL_LOGIN_SUBMIT = "#ctl00_ContentPlaceHolder1_AuthenticateIPA1_btnAuth"

# Smart Search dashboard
SEL_SAVED_SEARCHES_DROPDOWN = "#ctl00_ContentPlaceHolder1_as1_ddlSavedSearches"
SEL_PER_PAGE_DROPDOWN = 'select[name$="ddlPerPage"]'

# Search results (authenticated grid)
SEL_RESULTS_GRID = "#ctl00_ContentPlaceHolder1_WSExtendedGrid1_GridView1"
SEL_VIEW_BUTTON_PATTERN = "input[name$='btnView']"
SEL_NEXT_PAGE_BUTTON = "input[title='Next page']"
SEL_PAGE_INFO = "td:has-text('Page ')"

# Notice detail page
SEL_CAPTCHA_IFRAME = "iframe[src*='recaptcha']"
SEL_VIEW_NOTICE_BUTTON = "#ctl00_ContentPlaceHolder1_PublicNoticeDetailsBody1_btnViewNotice"
RECAPTCHA_SITEKEY = "6LdtSg8sAAAAADTdRyZxJ2R2sS82pKALNMvMqSyL"

# ── Rate Limiting ──────────────────────────────────────────────────────
REQUEST_DELAY_MIN = 2.0  # seconds between requests
REQUEST_DELAY_MAX = 3.0
MAX_RETRIES = 3
RESULTS_PER_PAGE = 50  # max the site allows

# ── Image Processing ───────────────────────────────────────────────────
_v = os.getenv("BLUR_THRESHOLD", "").strip()
BLUR_THRESHOLD = int(_v) if _v else 100   # Laplacian variance; below = rejected as blurry
TESSERACT_PSM_PDF = 3    # fully automatic — best for PDF tax sale tables
TESSERACT_PSM_PHOTO = 4  # assume single column of variable-size text — best for terminal screen photos

# ── Notice Types ───────────────────────────────────────────────────────
NOTICE_TYPES = ["foreclosure", "probate"]

# ── Philadelphia, PA — Source Definitions ─────────────────────────────

@dataclass
class PhillySource:
    """One Philadelphia distress-notice scrape source."""
    source_id: str       # e.g. "li_violations"
    notice_type: str     # e.g. "CODE_VIOLATION"
    county: str          # "Philadelphia"
    state: str           # "PA"
    description: str
    enabled: bool = True
    # ── Cadence metadata (consumed by GitHub Actions scheduler) ───────────
    # cadence:     "daily" | "twice_weekly" | "weekly" | "disabled"
    # run_days_et: comma-sep days for non-daily cadences, e.g. "Mon,Thu"
    # run_time_et: HH:MM in US/Eastern — staggered 15 min apart to avoid
    #              concurrent API/proxy load across sources.
    cadence: str = "daily"
    run_days_et: str = ""        # blank = every day (for "daily")
    run_time_et: str = ""        # HH:MM ET


# SOURCE 1 — L&I Code Violations (Carto SQL API)
PHILLY_CARTO_API_URL = "https://phl.carto.com/api/v2/sql"
PHILLY_CARTO_VIOLATIONS_TABLE = "violations"
PHILLY_CARTO_LOOKBACK_DAYS = 7

# SOURCE 2 — Sheriff Mortgage Foreclosure Sales (Bid4Assets)
# NOTE: migrated from salesweb.civilview.com in 2021 — do NOT use CivilView
# Bid4Assets uses Akamai WAF — plain Playwright returns "Access Denied".
# Set PHILLY_PROXY_URL to a residential proxy (e.g. Bright Data, Oxylabs)
# to bypass the CDN block.  Format: http://user:pass@host:port
PHILLY_PROXY_URL = os.getenv("PHILLY_PROXY_URL", "")
PHILLY_BID4ASSETS_MORTGAGE_URL = "https://www.bid4assets.com/philaforeclosures"

# SOURCE 3 — Sheriff Tax Sales (Bid4Assets, same parser as SOURCE 2)
PHILLY_BID4ASSETS_TAX_URL = "https://www.bid4assets.com/philataxsales"

# hCaptcha sitekey for Bid4Assets (Sources 2 & 3).
# The scraper also tries to read it live from the DOM via [data-sitekey];
# this constant is the fallback if the DOM attribute is absent.
BID4ASSETS_HCAPTCHA_SITEKEY = os.getenv("BID4ASSETS_HCAPTCHA_SITEKEY", "")

# SOURCE 4 — Lis Pendens / Pre-Foreclosure (FJD Civil eFiling)
# Strategy: scrape search-results page only — do NOT follow individual
# docket links ($5/report). Extract case caption, parties, filing date.
PHILLY_FJD_CIVIL_URL = "https://fjdefile.phila.gov/efsfjd/zk_fjd_public_qry_03.zp_dktrpt_frames"
PHILLY_FJD_CIVIL_LOOKBACK_DAYS = 7

# SOURCE 5 — Evictions (Philadelphia Municipal Court CLAIMS)
# Separate system from SOURCE 4. Case format: LT-MM-YY-NN-NNNN
# Lower priority — enabled separately after core sources validated.
PHILLY_FJD_CLAIMS_URL = "https://fjdclaims.phila.gov/phmuni/login.do"
PHILLY_FJD_CLAIMS_LOOKBACK_DAYS = 7

# SOURCE 6 — Probate / Estate Notices (Philadelphia Inquirer Marketplace)
# No auth required. Cross-reference decedent against OPA to drop non-property owners.
PHILLY_INQUIRER_ESTATE_URL = "https://marketplace.inquirer.com/pa/estate-notice/search"
PHILLY_INQUIRER_LOOKBACK_DAYS = 7

# OPA property lookup used by SOURCE 6 probate cross-reference.
# Queries the same Carto endpoint; OPA properties are in opa_properties_public.
PHILLY_OPA_TABLE = "opa_properties_public"

PHILLY_SOURCES: list[PhillySource] = [
    PhillySource(
        source_id="li_violations",
        notice_type="CODE_VIOLATION",
        county="Philadelphia",
        state="PA",
        description="L&I Code Violations — Carto SQL API (all violations, owner_status + count tags)",
        cadence="daily",
        run_time_et="07:00",
    ),
    PhillySource(
        source_id="inquirer_probate",
        notice_type="PROBATE_ESTATE",
        county="Philadelphia",
        state="PA",
        description="Probate / Estate Notices — Inquirer Marketplace (Filter 1 geo, fuzzy OPA name match)",
        cadence="daily",
        run_time_et="07:15",
    ),
    PhillySource(
        source_id="bid4assets_mortgage",
        notice_type="SHERIFF_MORTGAGE_FORECLOSURE",
        county="Philadelphia",
        state="PA",
        description="Sheriff Mortgage Foreclosure Sales — Bid4Assets (Scrapfly ASP+JS, inline JSON)",
        cadence="twice_weekly",
        run_days_et="Mon,Thu",
        run_time_et="07:30",
    ),
    PhillySource(
        source_id="bid4assets_tax",
        notice_type="TAX_SALE",
        county="Philadelphia",
        state="PA",
        description="Sheriff Tax Sales — Bid4Assets (Scrapfly ASP+JS, inline JSON)",
        cadence="twice_weekly",
        run_days_et="Mon,Thu",
        run_time_et="07:45",
    ),
    PhillySource(
        source_id="fjd_evictions",
        notice_type="EVICTION",
        county="Philadelphia",
        state="PA",
        description="Evictions — Philadelphia Municipal Court CLAIMS (eviction_count_for_owner, property_address via per-case CAPTCHA)",
        cadence="daily",
        run_time_et="08:00",
    ),
    # DEFERRED — FJD reCAPTCHA v3 score threshold unachievable via automated
    # means as of April 2026. Revisit if score threshold changes or alternative
    # authentication path becomes available.
    PhillySource(
        source_id="fjd_lis_pendens",
        notice_type="LIS_PENDENS",
        county="Philadelphia",
        state="PA",
        description="Lis Pendens / Pre-Foreclosure — FJD Civil eFiling (30-day lookback, 19 servicers)",
        enabled=False,
        cadence="disabled",
    ),
]


@dataclass
class SavedSearch:
    """Represents a saved search on tnpublicnotice.com."""
    county: str
    notice_type: str  # One of NOTICE_TYPES
    saved_search_name: str  # Exact name in the Saved Searches dropdown


# ── Saved Searches ─────────────────────────────────────────────────────
# These names must match exactly what appears in the dropdown on the site.
SAVED_SEARCHES: list[SavedSearch] = [
    SavedSearch("Knox", "foreclosure", "Foreclosure V2 Knox"),
    SavedSearch("Blount", "foreclosure", "Foreclosure V2 Blount"),
]

# ── Entity Detection ──────────────────────────────────────────────────
# Business entity patterns — shared across obituary_enricher, tax_enricher,
# and enrichment_pipeline for entity filtering.
BUSINESS_RE = re.compile(
    r"\b(?:LLC|L\.L\.C|INC|CORP|CORPORATION|COMPANY|CO\b|LTD|LP|L\.P|"
    r"PARTNERSHIP|ASSOCIATION|ASSOC|BANK|CREDIT UNION|CHURCH|MINISTRIES|"
    r"HOUSING|AUTHORITY|DEVELOPMENT|ENTERPRISES|PROPERTIES|INVESTMENTS|"
    r"GROUP|HOLDINGS|MANAGEMENT|SERVICES|FOUNDATION|ORGANIZATION)\b",
    re.IGNORECASE,
)

# Trust/estate patterns — personal trusts are NOT business entities
TRUST_NAME_RE = re.compile(
    r"^(?:THE\s+)?([\w]+(?:\s+[\w.]+)+?)\s+(?:REVOCABLE\s+)?(?:LIVING\s+)?TRUST\b",
    re.IGNORECASE,
)
ESTATE_OF_RE = re.compile(
    r"^(?:THE\s+)?ESTATE\s+OF\s+([\w]+(?:\s+[\w.]+)+?)(?:\s*,|\s*$)",
    re.IGNORECASE,
)

_config_logger = logging.getLogger(__name__)


# ── State File Utilities ─────────────────────────────────────────────


def save_state(path: Path, data: dict) -> None:
    """Write JSON state to disk atomically (write tmp → rename).

    Creates a .bak copy of the previous file before overwriting.
    """
    # Back up current file
    if path.exists():
        try:
            bak = path.with_suffix(path.suffix + ".bak")
            bak.write_bytes(path.read_bytes())
        except OSError:
            pass  # Best-effort backup

    # Atomic write: tmp → rename
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, indent=2), encoding="utf-8")
    tmp.replace(path)


def load_state(path: Path) -> dict:
    """Load JSON state from disk, falling back to .bak if corrupt."""
    for candidate in [path, path.with_suffix(path.suffix + ".bak")]:
        if candidate.exists():
            try:
                return json.loads(candidate.read_text(encoding="utf-8"))
            except (json.JSONDecodeError, OSError) as e:
                _config_logger.warning("Failed to read %s: %s", candidate, e)
    return {}
