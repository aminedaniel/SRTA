"""Point-in-time normalization and features for public SEC Form 13F disclosures."""

from __future__ import annotations

import math
import xml.etree.ElementTree as ET
from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from enum import StrEnum


class AmendmentType(StrEnum):
    """The two amendment types published in a Form 13F cover page."""

    NONE = "none"
    RESTATEMENT = "restatement"
    NEW_HOLDINGS = "new_holdings"


@dataclass(frozen=True)
class Form13FHolding:
    reporting_quarter: date
    filing_date: date
    accession_number: str
    amendment_type: AmendmentType
    issuer: str
    cusip: str
    ticker: str | None
    shares: int
    reported_value_usd: int

    @property
    def is_amendment(self) -> bool:
        return self.amendment_type is not AmendmentType.NONE


@dataclass(frozen=True)
class PositionActivity:
    holding: Form13FHolding | None
    status: str
    share_change_percent: float
    consecutive_quarters_held: int
    position_size_percentile: float
    disclosure_age_days: int
    evidence_reporting_quarter: date | None
    evidence_filing_date: date | None

    def feature_values(self) -> dict[str, float | int | str]:
        """Return non-null values suitable for a deterministic FeatureSnapshot."""
        return {
            "renaissance_13f_status": self.status,
            "renaissance_13f_share_change_percent": self.share_change_percent,
            "renaissance_13f_consecutive_quarters_held": self.consecutive_quarters_held,
            "renaissance_13f_position_size_percentile": self.position_size_percentile,
            "renaissance_13f_disclosure_age_days": self.disclosure_age_days,
            "renaissance_13f_lag_decay": disclosure_lag_decay(self),
        }


def parse_amendment_type(form: str, cover_xml: bytes | str | None = None) -> AmendmentType:
    """Read the SEC cover-page amendment type, rejecting unknown amended forms."""
    if form != "13F-HR/A":
        return AmendmentType.NONE
    if cover_xml is None:
        raise ValueError("13F-HR/A requires its SEC amendment type")
    try:
        root = ET.fromstring(cover_xml)
    except ET.ParseError as error:
        raise ValueError("Invalid SEC 13F cover-page XML") from error
    value = _text(root, "amendmentType").lower().replace(" ", "_")
    mapping = {"restatement": AmendmentType.RESTATEMENT, "new_holdings": AmendmentType.NEW_HOLDINGS}
    if value not in mapping:
        raise ValueError(f"Unsupported 13F amendment type: {value or 'missing'}")
    return mapping[value]


def _text(element: ET.Element, name: str) -> str:
    match = next((child for child in element.iter() if child.tag.rsplit("}", 1)[-1] == name), None)
    return (match.text or "").strip() if match is not None else ""


def parse_information_table(
    xml: bytes | str,
    *,
    reporting_quarter: date,
    filing_date: date,
    accession_number: str,
    amendment_type: AmendmentType = AmendmentType.NONE,
    ticker_for_cusip: Callable[[str], str | None] | None = None,
) -> list[Form13FHolding]:
    """Parse and aggregate reportable long public-equity rows from an EDGAR table.

    A filing can split one security across voting-discretion rows.  Only SH
    rows without a put/call marker are treated as public-equity corroboration.
    """
    try:
        root = ET.fromstring(xml)
    except ET.ParseError as error:
        raise ValueError("Invalid SEC 13F information-table XML") from error

    aggregated: dict[str, tuple[str, int, int]] = {}
    for item in (node for node in root.iter() if node.tag.rsplit("}", 1)[-1] == "infoTable"):
        cusip = _text(item, "cusip").upper()
        issuer = _text(item, "nameOfIssuer")
        shares_text, value_text = (
            _text(item, "sshPrnamt").replace(",", ""),
            _text(item, "value").replace(",", ""),
        )
        security_type = _text(item, "sshPrnamtType").upper()
        if not cusip or not issuer or not shares_text or not value_text or security_type != "SH":
            continue
        if _text(item, "putCall"):
            continue
        try:
            shares, value = int(float(shares_text)), int(float(value_text) * 1_000)
        except ValueError:
            continue
        old_issuer, old_shares, old_value = aggregated.get(cusip, (issuer, 0, 0))
        aggregated[cusip] = (old_issuer, old_shares + shares, old_value + value)

    return [
        Form13FHolding(
            reporting_quarter,
            filing_date,
            accession_number,
            amendment_type,
            issuer,
            cusip,
            ticker_for_cusip(cusip) if ticker_for_cusip else None,
            shares,
            value,
        )
        for cusip, (issuer, shares, value) in aggregated.items()
    ]


def latest_public_filings(holdings: list[Form13FHolding], as_of: date) -> list[Form13FHolding]:
    """Use only public filings, applying 13F restatements and new-holdings amendments."""
    by_quarter: dict[date, list[Form13FHolding]] = defaultdict(list)
    for row in holdings:
        if row.filing_date <= as_of:
            by_quarter[row.reporting_quarter].append(row)
    selected: list[Form13FHolding] = []
    for rows in by_quarter.values():
        by_filing: dict[str, list[Form13FHolding]] = defaultdict(list)
        for row in rows:
            by_filing[row.accession_number].append(row)
        filings = sorted(by_filing.values(), key=lambda group: group[0].filing_date)
        base: dict[str, Form13FHolding] = {}
        for filing in filings:
            amendment = filing[0].amendment_type
            if amendment in {AmendmentType.NONE, AmendmentType.RESTATEMENT}:
                base = {row.cusip: row for row in filing}
            else:  # NEW_HOLDINGS supplements rather than replaces the base report.
                base.update({row.cusip: row for row in filing})
        selected.extend(base.values())
    return selected


def _ticker_positions(rows: list[Form13FHolding]) -> dict[str, Form13FHolding]:
    """Aggregate every reportable CUSIP mapped to each ticker."""
    grouped: dict[str, list[Form13FHolding]] = defaultdict(list)
    for row in rows:
        if row.ticker:
            grouped[row.ticker.upper()].append(row)
    return {
        ticker: Form13FHolding(
            group[0].reporting_quarter,
            group[0].filing_date,
            group[0].accession_number,
            group[0].amendment_type,
            group[0].issuer,
            ",".join(sorted(row.cusip for row in group)),
            ticker,
            sum(row.shares for row in group),
            sum(row.reported_value_usd for row in group),
        )
        for ticker, group in grouped.items()
    }


def derive_position_activity(
    holdings: list[Form13FHolding], *, ticker: str, as_of: date
) -> PositionActivity:
    """Derive a ticker's activity using only disclosures available at ``as_of``."""
    public = latest_public_filings(holdings, as_of)
    quarters = sorted({row.reporting_quarter for row in public}, reverse=True)
    if not quarters:
        return PositionActivity(None, "none", 0.0, 0, 0.0, 0, None, None)
    current_quarter = quarters[0]
    portfolio = [row for row in public if row.reporting_quarter == current_quarter]
    positions = _ticker_positions(portfolio)
    current = positions.get(ticker.upper())
    prior_quarter = quarters[1] if len(quarters) > 1 else None
    prior_positions = _ticker_positions(
        [row for row in public if row.reporting_quarter == prior_quarter]
    )
    prior = prior_positions.get(ticker.upper())
    if current and not prior:
        status, change = "new", 0.0
    elif not current and prior:
        status, change = "exited", -100.0
    elif current and prior:
        change = (current.shares - prior.shares) / prior.shares * 100 if prior.shares else 0.0
        status = "increased" if change > 0 else "reduced" if change < 0 else "unchanged"
    else:
        return PositionActivity(None, "none", 0.0, 0, 0.0, 0, None, None)
    target = current or prior
    assert target is not None
    held = 0
    for quarter in quarters:
        if ticker.upper() in _ticker_positions(
            [row for row in public if row.reporting_quarter == quarter]
        ):
            held += 1
        else:
            break
    percentile = 0.0
    if current and positions:
        percentile = (
            sum(row.reported_value_usd <= current.reported_value_usd for row in positions.values())
            / len(positions)
            * 100
        )
    # An exit becomes knowable only when the subsequent report is filed.
    evidence = current or (portfolio[0] if portfolio else target)
    return PositionActivity(
        target,
        status,
        change,
        held,
        percentile,
        max(0, (as_of - evidence.filing_date).days),
        evidence.reporting_quarter,
        evidence.filing_date,
    )


def disclosure_lag_decay(activity: PositionActivity) -> float:
    """Decay delayed 13F evidence; 45 days is the statutory filing window."""
    if activity.holding is None:
        return 0.0
    if activity.evidence_filing_date is None or activity.evidence_reporting_quarter is None:
        return 0.0
    lag = max(0, (activity.evidence_filing_date - activity.evidence_reporting_quarter).days)
    return math.exp(-lag / 45.0) * math.exp(-activity.disclosure_age_days / 120.0)
