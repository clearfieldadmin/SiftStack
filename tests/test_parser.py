"""Unit test for notice_parser against real captured notice text.

Run: .venv/Scripts/python.exe tests/test_parser.py
"""

import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from notice_parser import (
    NoticeData,
    _extract_notice_content,
    _extract_publish_date,
    _parse_address,
    _parse_name,
    _normalize_date,
)

# ── Real notice text captured from live test (Notice ID 509975) ────────

FULL_PAGE_TEXT = """About Public Notices
|
Help
Welcome, ty@volunteerhomebuyers.com
|
Change password
|
Sign Out
|
My Smart Search
For more Public Notices visit: usalegalnotice.com
 > Search Results > Public Notice Detail
News Sentinel\xa0

Publication Name:
News Sentinel

Publication URL:


Publication City and State:
Knoxville, TN

Publication County:
Knox

Notice Popular Keyword Category:


Notice Keywords:
real+estate foreclosure foreclosed foreclose judicial+sale judgment notice+of+sale forfeiture forfeit magistrate+sale

Notice Authentication Number:
202602222008240194710
1816537541

Notice URL:


Back


\tNotice Publish Date:
\tFriday, February 20, 2026

Notice Content

SUCCESSOR TRUSTEE'S NOTICE OF SALE OF REAL ESTATE \xa0 ANTHONY R. STEELE is the Successor Trustee of a Deed of Trust executed on November 4, 2016, by DANIEL H. WILLIAMS, a single person. The deed of trust appears of record in the Register's Office of Knox County, Tennessee, at Instrument 201611180032554. The Successor Trustee will sell the property described below for cash at a foreclosure sale requested by the current holder of the Deed of Trust and underlying indebtedness, Truist Bank, successor by merger to SunTrust Bank. Sale Date and Location: MARCH 6, 2026, at 2:00 p.m. at the Knox County Courthouse, designated as near the Main Assembly Room inside the northernmost entrance from Main Avenue to the City-County Building in Knoxville, Knox County, TN. The terms of sale shall be payment by cashier's check or certified funds immediately upon conclusion of the sale. Third-party internet posting website: foreclosuretennessee.com Property Description: Abbreviated description per TCA 35-5-104(a)(2) is the property described in the Deed of Trust and additionally referenced at Instrument 200004240026535, and commonly known as 5100 Stokely Ln., Knoxville, Knox County, TN 37918. Property Address: 5100 Stokely Ln., Knoxville, Knox County, TN 37918. Tax Map Identification No.: 049GE011 Parties Interested: NONE KNOWN. All sales of Property are "AS IS" and "WHERE IS" without representation or warranty. THE PURPOSE OF THIS COMMUNICATION IS TO COLLECT THE DEBT. This the 6TH day of February, 2026. \xa0 Anthony R. Steele, Successor Trustee \xa0 Winchester, Sellers, Foster & Steele, P.C. P. O. Box 2428 Knoxville, TN 37901 (865) 637-1980 Publication Dates: February 13 and 20, 2026.

Back

If you have any questions please send an email to the administrator.

Copyright Tennessee Press Association 2021

Select Language
English
Spanish"""


def test_extract_notice_content():
    content = _extract_notice_content(FULL_PAGE_TEXT)
    assert content.startswith("SUCCESSOR TRUSTEE"), f"Got: {content[:80]}"
    assert "DANIEL H. WILLIAMS" in content
    assert "5100 Stokely Ln" in content
    # Should NOT include footer
    assert "If you have any questions" not in content
    assert "Select Language" not in content
    print("PASS: _extract_notice_content")


def test_extract_publish_date():
    date = _extract_publish_date(FULL_PAGE_TEXT)
    assert date == "2026-02-20", f"Got: {date}"
    print("PASS: _extract_publish_date -> 2026-02-20")


def test_normalize_date():
    assert _normalize_date("February 20, 2026") == "2026-02-20"
    assert _normalize_date("2/20/2026") == "2026-02-20"
    assert _normalize_date("02-20-2026") == "2026-02-20"
    print("PASS: _normalize_date")


def test_parse_address():
    notice = NoticeData(raw_text=_extract_notice_content(FULL_PAGE_TEXT))
    _parse_address(notice)

    print(f"  address:  {notice.address!r}")
    print(f"  city:     {notice.city!r}")
    print(f"  zip:      {notice.zip!r}")

    assert "5100" in notice.address, f"Expected 5100 in address, got: {notice.address}"
    assert "Stokely" in notice.address, f"Expected Stokely in address, got: {notice.address}"
    assert notice.city == "Knoxville", f"Expected Knoxville, got: {notice.city}"
    assert notice.zip == "37918", f"Expected 37918, got: {notice.zip}"
    print("PASS: _parse_address")


def test_parse_name_foreclosure():
    content = _extract_notice_content(FULL_PAGE_TEXT)
    notice = NoticeData(raw_text=content, notice_type="foreclosure")
    _parse_name(notice)

    print(f"  owner_name: {notice.owner_name!r}")

    assert "Daniel" in notice.owner_name, f"Expected Daniel in name, got: {notice.owner_name}"
    assert "Williams" in notice.owner_name, f"Expected Williams in name, got: {notice.owner_name}"
    print("PASS: _parse_name (foreclosure)")


if __name__ == "__main__":
    print("=" * 60)
    print("Testing notice_parser against real captured notice text")
    print("=" * 60)
    print()

    test_extract_notice_content()
    test_extract_publish_date()
    test_normalize_date()
    test_parse_address()
    test_parse_name_foreclosure()

    print()
    print("All tests passed!")
