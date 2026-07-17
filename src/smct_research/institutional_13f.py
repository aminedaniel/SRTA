"""Point-in-time normalization and features for public SEC Form 13F disclosures."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Form13FHolding:
    reporting_quarter: date
    filing_date: date
    accession_number: str
    is_amendment: bool
    issuer: str
    cusip: str
    ticker: str | None
    shares: int
    reported_value_usd: int


@dataclass(frozen=True)
class PositionActivity:
    holding: Form13FHolding | None
    status: str
    share_change_percent: float | None
    consecutive_quarters_held: int
    position_size_percentile: float | None
    disclosure_age_days: int


def parse_information_table(
    xml: bytes | str,
    *,
    reporting_quarter: date,
    filing_date: date,
    accession_number: str,
    is_amendment: bool,
    ticker_for_cusip: Callable[[str], str | None] | None = None,
) -> list[Form13FHolding]:
    """Parse an EDGAR 13F information-table XML document without network I/O."""
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as error:
        raise ValueError("Invalid SEC 13F information-table XML") from error

    def text(element: ET.Element, name: str) -> str:
        match = next(
            (child for child in element.iter() if child.tag.rsplit("}", 1)[-1] == name), None
        )
        return (match.text or "").strip() if match is not None else ""

    holdings: list[Form13FHolding] = []
    for item in (node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "infoTable"):
        cusip = text(item, "cusip").upper()
        shares_text = text(item, "sshPrnamt").replace(",", "")
        value_text = text(item, "value").replace(",", "")
        if not cusip or not shares_text or not value_text:
            continue
        # SEC 13F's value field is reported in thousands of dollars.
        holdings.append(
            Form13FHolding(
                reporting_quarter=reporting_quarter,
                filing_date=filing_date,
                accession_number=accession_number,
                is_amendment=is_amendment,
                issuer=text(item, "nameOfIssuer"),
                cusip=cusip,
                ticker=ticker_for_cusip(cusip) if ticker_for_cusip else None,
                shares=int(float(shares_text)),
                reported_value_usd=int(float(value_text) * 1_000),
            )
        )
    return holdings


def latest_public_filings(holdings: list[Form13FHolding], as_of: date) -> list[Form13FHolding]:
    """Return only filing versions public by ``as_of``; amendments supersede originals."""
    eligible = [item for item in holdings if item.filing_date <= as_of]
    selected: dict[tuple[date, str], Form13FHolding] = {}
    # An amended filing replaces the original 13F for the same reporting period.
    for item in sorted(
        eligible, key=lambda row: (row.reporting_quarter, row.filing_date, row.is_amendment)
    ):
        key = (item.reporting_quarter, item.cusip)
        existing = selected.get(key)
        if existing is None or item.is_amendment or item.filing_date >= existing.filing_date:
            selected[key] = item
    return list(selected.values())


def derive_position_activity(
    holdings: list[Form13FHolding], *, ticker: str, as_of: date
) -> PositionActivity:
    """Derive a ticker's activity using only disclosures available at ``as_of``."""
    public = latest_public_filings(holdings, as_of)
    quarters = sorted({row.reporting_quarter for row in public}, reverse=True)
    if not quarters:
        return PositionActivity(None, "none", None, 0, None, 0)
    current_quarter = quarters[0]
    portfolio = [row for row in public if row.reporting_quarter == current_quarter]
    current = next((row for row in portfolio if row.ticker == ticker.upper()), None)
    prior_quarter = quarters[1] if len(quarters) > 1 else None
    prior = (
        next(
            (
                row
                for row in public
                if row.reporting_quarter == prior_quarter and row.ticker == ticker.upper()
            ),
            None,
        )
        if prior_quarter
        else None
    )
    if current and not prior:
        status, change = "new", None
    elif not current and prior:
        status, change = "exited", -100.0
    elif current and prior:
        status, change = (
            ("increased" if current.shares > prior.shares else "reduced"),
            ((current.shares - prior.shares) / prior.shares * 100 if prior.shares else None),
        )
    else:
        return PositionActivity(None, "none", None, 0, None, 0)

    target = current or prior
    assert target is not None
    held = 0
    for quarter in quarters:
        if any(row.reporting_quarter == quarter and row.ticker == ticker.upper() for row in public):
            held += 1
        else:
            break
    values = sorted(row.reported_value_usd for row in portfolio)
    percentile = (
        sum(value <= target.reported_value_usd for value in values) / len(values) * 100
        if current
        else None
    )
    return PositionActivity(
        holding=target,
        status=status,
        share_change_percent=change,
        consecutive_quarters_held=held,
        position_size_percentile=percentile,
        disclosure_age_days=max(0, (as_of - target.filing_date).days),
    )


def disclosure_lag_decay(activity: PositionActivity) -> float:
    """Decay delayed 13F evidence; 45 days is the statutory filing window."""
    if activity.holding is None:
        return 0.0
    lag = max(0, (activity.holding.filing_date - activity.holding.reporting_quarter).days)
    return math.exp(-lag / 45.0) * math.exp(-activity.disclosure_age_days / 120.0)
