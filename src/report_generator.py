"""Generate a PDF deep-prospecting report for a single enriched record.

Produces a one-page (or multi-page) PDF with property summary, deceased owner
detection, signing chain with skip-trace contacts, and valuation data.
Designed for upload to Google Drive with a link embedded in DataSift Notes.
"""

import json
import logging
import re
from datetime import datetime
from pathlib import Path

from reportlab.lib import colors
from reportlab.lib.pagesizes import letter
from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
from reportlab.lib.units import inch
from reportlab.platypus import (
    Paragraph,
    SimpleDocTemplate,
    Spacer,
    Table,
    TableStyle,
)

from notice_parser import NoticeData

logger = logging.getLogger(__name__)

# ── Styles ──────────────────────────────────────────────────────────────

_styles = getSampleStyleSheet()

TITLE_STYLE = ParagraphStyle(
    "ReportTitle",
    parent=_styles["Title"],
    fontSize=16,
    spaceAfter=4,
)

SECTION_STYLE = ParagraphStyle(
    "SectionHeader",
    parent=_styles["Heading2"],
    fontSize=12,
    spaceBefore=12,
    spaceAfter=4,
    textColor=colors.HexColor("#1a1a2e"),
)

BODY_STYLE = ParagraphStyle(
    "BodyText",
    parent=_styles["Normal"],
    fontSize=9,
    leading=12,
)

SMALL_STYLE = ParagraphStyle(
    "SmallText",
    parent=_styles["Normal"],
    fontSize=8,
    leading=10,
    textColor=colors.grey,
)

SIGNER_NAME_STYLE = ParagraphStyle(
    "SignerName",
    parent=_styles["Normal"],
    fontSize=9,
    leading=12,
    fontName="Helvetica-Bold",
)


# ── Helpers ─────────────────────────────────────────────────────────────

def _val(value: str, fallback: str = "—") -> str:
    """Return value or fallback if empty."""
    return value.strip() if value and value.strip() else fallback


def _money(value: str) -> str:
    """Format a numeric string as currency."""
    if not value:
        return "—"
    try:
        return f"${int(float(value)):,}"
    except (ValueError, TypeError):
        return value


def _kv_table(data: list[tuple[str, str]], col_widths=None) -> Table:
    """Build a simple key-value table."""
    if col_widths is None:
        col_widths = [2.0 * inch, 4.5 * inch]

    table_data = []
    for label, value in data:
        table_data.append([
            Paragraph(f"<b>{label}</b>", BODY_STYLE),
            Paragraph(value, BODY_STYLE),
        ])

    t = Table(table_data, colWidths=col_widths)
    t.setStyle(TableStyle([
        ("VALIGN", (0, 0), (-1, -1), "TOP"),
        ("TOPPADDING", (0, 0), (-1, -1), 1),
        ("BOTTOMPADDING", (0, 0), (-1, -1), 1),
        ("LEFTPADDING", (0, 0), (0, -1), 0),
    ]))
    return t


def _address_slug(notice: NoticeData) -> str:
    """Create a filesystem-safe slug from the property address."""
    addr = (notice.address or "unknown").lower()
    slug = re.sub(r"[^a-z0-9]+", "_", addr).strip("_")
    return slug[:50]


# ── Main generator ──────────────────────────────────────────────────────

def generate_record_pdf(
    notice: NoticeData,
    output_dir: Path | None = None,
    phone_tiers: dict | None = None,
) -> Path:
    """Generate a PDF deep-prospecting report for a single record.

    Args:
        notice: Fully enriched NoticeData object.
        output_dir: Directory for the PDF (default: output/reports/).
        phone_tiers: Optional dict mapping cleaned phone → {score, tier, line_type}
                     from Trestle validation. If None, phones shown without tiers.

    Returns:
        Path to the generated PDF file.
    """
    if output_dir is None:
        output_dir = Path("output/reports")
    output_dir.mkdir(parents=True, exist_ok=True)

    date_str = datetime.now().strftime("%Y-%m-%d")
    filename = f"{_address_slug(notice)}_{date_str}.pdf"
    pdf_path = output_dir / filename

    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        leftMargin=0.75 * inch,
        rightMargin=0.75 * inch,
        topMargin=0.6 * inch,
        bottomMargin=0.6 * inch,
    )

    story = []

    # ── Title ──
    story.append(Paragraph("Deep Prospecting Report", TITLE_STYLE))
    story.append(Paragraph(
        f"Generated {date_str} &nbsp;|&nbsp; {_val(notice.address)}, "
        f"{_val(notice.city)} {_val(notice.state)} {_val(notice.zip)}",
        SMALL_STYLE,
    ))
    story.append(Spacer(1, 8))

    # ── Property Summary ──
    story.append(Paragraph("Property Summary", SECTION_STYLE))
    story.append(_kv_table([
        ("Address", f"{_val(notice.address)}, {_val(notice.city)}, "
                    f"{_val(notice.state)} {_val(notice.zip)}"),
        ("County", _val(notice.county)),
        ("Parcel ID", _val(notice.parcel_id)),
        ("Type", _val(notice.property_type)),
        ("Beds / Baths", f"{_val(notice.bedrooms)} / {_val(notice.bathrooms)}"),
        ("Sqft", _val(notice.sqft)),
        ("Year Built", _val(notice.year_built)),
        ("Lot Size", _val(notice.lot_size)),
    ]))

    # ── Notice ──
    story.append(Paragraph("Notice", SECTION_STYLE))
    story.append(_kv_table([
        ("Type", _val(notice.notice_type).replace("_", " ").title()),
        ("Date Added", _val(notice.date_added)),
        ("Auction Date", _val(notice.auction_date)),
        ("Owner on Title", _val(notice.owner_name)),
        ("Source", _val(notice.source_url)),
    ]))

    # ── Valuation ──
    story.append(Paragraph("Valuation", SECTION_STYLE))
    story.append(_kv_table([
        ("Estimated Value", _money(notice.estimated_value)),
        ("Estimated Equity", _money(notice.estimated_equity)),
        ("Equity %", f"{_val(notice.equity_percent)}%"
                     if notice.equity_percent else "—"),
        ("MLS Status", _val(notice.mls_status)),
        ("Last Sold", f"{_val(notice.mls_last_sold_date)} @ "
                      f"{_money(notice.mls_last_sold_price)}"
                      if notice.mls_last_sold_date else "—"),
    ]))

    # ── Deceased Owner Detection ──
    if notice.owner_deceased == "yes":
        story.append(Paragraph("Deceased Owner Detection", SECTION_STYLE))
        story.append(_kv_table([
            ("Status", "DECEASED"),
            ("Date of Death", _val(notice.date_of_death)),
            ("Obituary", _val(notice.obituary_url)),
            ("Source Type", _val(notice.obituary_source_type)),
            ("Confidence", f"{_val(notice.dm_confidence).upper()} — "
                           f"{_val(notice.dm_confidence_reason)}"
                           if notice.dm_confidence else "—"),
        ]))

        # ── Decision Maker ──
        story.append(Paragraph("Decision Maker (Primary Contact)", SECTION_STYLE))
        dm_addr = ""
        if notice.decision_maker_street:
            dm_addr = (f"{notice.decision_maker_street}, "
                       f"{_val(notice.decision_maker_city)}, "
                       f"{_val(notice.decision_maker_state)} "
                       f"{_val(notice.decision_maker_zip)}")
        story.append(_kv_table([
            ("Name", _val(notice.decision_maker_name)),
            ("Relationship", _val(notice.decision_maker_relationship)),
            ("Status", _val(notice.decision_maker_status).replace("_", " ").title()),
            ("Source", _val(notice.decision_maker_source).replace("_", " ").title()),
            ("Mailing Address", dm_addr or "—"),
        ]))

        # ── Signing Chain ──
        story.append(Paragraph(
            f"Signing Chain ({_val(notice.signing_chain_count, '0')} heirs must sign)",
            SECTION_STYLE,
        ))
        _add_signing_chain(story, notice, phone_tiers)

    # ── Tax Delinquency ──
    if notice.tax_delinquent_amount:
        story.append(Paragraph("Tax Delinquency", SECTION_STYLE))
        story.append(_kv_table([
            ("Amount Due", _money(notice.tax_delinquent_amount)),
            ("Years Delinquent", _val(notice.tax_delinquent_years)),
            ("Tax Owner", _val(notice.tax_owner_name)),
        ]))

    # ── Footer ──
    story.append(Spacer(1, 16))
    story.append(Paragraph(
        f"SiftStack — Deep Prospecting Report — {date_str}",
        SMALL_STYLE,
    ))

    doc.build(story)
    logger.info("PDF report generated: %s", pdf_path)
    return pdf_path


def _add_signing_chain(
    story: list,
    notice: NoticeData,
    phone_tiers: dict | None,
) -> None:
    """Add signing chain heirs with contact info to the PDF story."""
    if not notice.heir_map_json:
        story.append(Paragraph("(no heir map generated)", BODY_STYLE))
        return

    try:
        heirs = json.loads(notice.heir_map_json)
    except (json.JSONDecodeError, TypeError):
        story.append(Paragraph("(heir map parse error)", BODY_STYLE))
        return

    signers = [h for h in heirs
               if h.get("signing_authority") and h.get("status") != "deceased"]
    non_signers = [h for h in heirs
                   if not h.get("signing_authority") or h.get("status") == "deceased"]

    # Import here to avoid circular dep at module level
    from phone_validator import clean_phone

    for i, h in enumerate(signers, 1):
        name = h.get("name", "?")
        rel = h.get("relationship", "?")
        status = ("ALIVE" if h.get("status") == "verified_living"
                  else h.get("status", "?").upper())

        story.append(Paragraph(f"#{i} {name} ({rel}) — {status}", SIGNER_NAME_STYLE))

        rows = []

        # Address
        if h.get("street"):
            addr = (f"{h['street']}, {h.get('city', '')}, "
                    f"{h.get('state', '')} {h.get('zip', '')}")
            rows.append(("Address", addr))

        # Phones — from heir_map_json or flat fields for DM #1
        phones = h.get("phones", [])
        if i == 1 and not phones:
            for field in ["primary_phone", "mobile_1", "mobile_2", "mobile_3",
                          "mobile_4", "mobile_5", "landline_1", "landline_2",
                          "landline_3"]:
                val = getattr(notice, field, "")
                if val:
                    phones.append(val)

        for j, ph in enumerate(phones, 1):
            tier_str = ""
            if phone_tiers:
                cleaned = clean_phone(ph)
                info = phone_tiers.get(cleaned, {})
                tier = info.get("tier", "")
                score = info.get("score", "")
                line_type = info.get("line_type", "")
                if tier:
                    tier_str = f"  [{tier}, score={score}, {line_type}]"
            rows.append((f"Phone {j}", f"{ph}{tier_str}"))

        # Emails
        emails = h.get("emails", [])
        for j, em in enumerate(emails, 1):
            rows.append((f"Email {j}", em))

        if not phones and not emails:
            rows.append(("Contact", "(no phone or email found)"))

        story.append(_kv_table(rows, col_widths=[1.2 * inch, 5.3 * inch]))
        story.append(Spacer(1, 4))

    # Other family (non-signers) — compact
    if non_signers:
        story.append(Spacer(1, 6))
        story.append(Paragraph(
            f"Other Family ({len(non_signers)} — no signing authority)",
            ParagraphStyle("OtherFamilyHeader", parent=BODY_STYLE,
                           fontName="Helvetica-Bold", fontSize=9),
        ))
        lines = []
        for h in non_signers[:8]:
            status = ("living" if h.get("status") == "verified_living"
                      else h.get("status", "?"))
            lines.append(f"{h.get('name', '?')} ({h.get('relationship', '?')}) [{status}]")
        if len(non_signers) > 8:
            lines.append(f"... and {len(non_signers) - 8} more")
        story.append(Paragraph("<br/>".join(lines), SMALL_STYLE))
