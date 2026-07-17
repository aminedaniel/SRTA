"""SEC Form 4 normalization and point-in-time cluster-buying features.

Form 4 purchases are corroborating research evidence, never standalone trade
instructions.  Features use a filing date cutoff, so an event is unavailable
until the SEC made it public.
"""

from __future__ import annotations

from collections import defaultdict
from datetime import date
from xml.etree import ElementTree as ET

from pydantic import BaseModel, Field, model_validator


class Form4Transaction(BaseModel):
    issuer_cik: str
    ticker: str
    insider_name: str
    reporting_owner_cik: str
    report_date: date
    insider_role: str = ""
    is_officer: bool = False
    is_director: bool = False
    is_ten_percent_owner: bool = False
    transaction_date: date
    filing_date: date
    transaction_code: str
    acquired_disposed: str
    shares: float = Field(gt=0)
    price_per_share: float = Field(gt=0)
    transaction_value: float = Field(gt=0)
    ownership_nature: str
    accession_number: str
    is_amendment: bool = False

    @model_validator(mode="after")
    def normalize(self) -> Form4Transaction:
        self.issuer_cik = self.issuer_cik.lstrip("0") or "0"
        self.ticker = self.ticker.upper().strip()
        self.insider_name = self.insider_name.strip()
        self.reporting_owner_cik = self.reporting_owner_cik.lstrip("0") or "0"
        self.transaction_code = self.transaction_code.upper().strip()
        self.acquired_disposed = self.acquired_disposed.upper().strip()
        self.ownership_nature = self.ownership_nature.upper().strip()
        return self

    @property
    def is_qualifying_purchase(self) -> bool:
        return (
            self.transaction_code == "P"
            and self.acquired_disposed == "A"
            and self.shares > 0
            and self.price_per_share > 0
        )

    @property
    def purchase_event_key(self) -> tuple[str, str, date, str, float, float]:
        """Identity of a purchase event, independent of direct/indirect split rows."""
        return (
            self.issuer_cik,
            self.reporting_owner_cik,
            self.transaction_date,
            self.transaction_code,
            self.shares,
            self.price_per_share,
        )

    @property
    def amendment_filing_key(self) -> tuple[str, str, date]:
        return (self.issuer_cik, self.reporting_owner_cik, self.report_date)


class Form4Filing(BaseModel):
    """Filing-level metadata retained even when it contains no qualifying buy."""

    issuer_cik: str
    reporting_owner_ciks: tuple[str, ...]
    report_date: date
    filing_date: date
    accession_number: str
    is_amendment: bool = False
    is_joint_filing: bool = False
    transactions: list[Form4Transaction] = Field(default_factory=list)

    @model_validator(mode="after")
    def normalize(self) -> Form4Filing:
        self.issuer_cik = self.issuer_cik.lstrip("0") or "0"
        self.reporting_owner_ciks = tuple(
            cik.lstrip("0") or "0" for cik in self.reporting_owner_ciks
        )
        return self

    @property
    def amendment_filing_key(self) -> tuple[str, str, date] | None:
        if len(self.reporting_owner_ciks) != 1:
            return None
        return (self.issuer_cik, self.reporting_owner_ciks[0], self.report_date)


def _text(element: ET.Element, path: str) -> str:
    node = element.find(path)
    return (node.text or "").strip() if node is not None else ""


def _number(value: str) -> float:
    try:
        return float(value.replace(",", ""))
    except ValueError:
        return 0.0


def parse_form4_xml(
    payload: bytes, *, filing_date: date, accession_number: str, is_amendment: bool = False
) -> list[Form4Transaction]:
    """Parse non-derivative Form 4 transactions; callers retain qualifying P buys."""
    root = ET.fromstring(payload)
    # EDGAR ownership XML commonly uses a default namespace; normalize tags so
    # the parser works identically for namespaced and fixture documents.
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    issuer_cik = _text(root, ".//issuer/issuerCik")
    ticker = _text(root, ".//issuer/issuerTradingSymbol")
    owners = root.findall(".//reportingOwner")
    # A transaction table in a joint filing cannot be attributed reliably to an
    # individual reporting owner. Exclude it rather than inflate buyer breadth.
    if len(owners) != 1:
        return []
    owner = owners[0]
    insider_name = _text(owner, ".//reportingOwnerId/rptOwnerName")
    reporting_owner_cik = _text(owner, ".//reportingOwnerId/rptOwnerCik")
    report_date_text = _text(root, ".//periodOfReport")
    report_date = date.fromisoformat(report_date_text) if report_date_text else filing_date
    relationship = owner.find(".//reportingOwnerRelationship")
    is_officer = _text(relationship, "isOfficer") == "1" if relationship is not None else False
    is_director = _text(relationship, "isDirector") == "1" if relationship is not None else False
    is_ten = _text(relationship, "isTenPercentOwner") == "1" if relationship is not None else False
    title = _text(relationship, "officerTitle") if relationship is not None else ""
    roles = ", ".join(
        x
        for x in [
            "officer" if is_officer else "",
            "director" if is_director else "",
            "10% owner" if is_ten else "",
            title,
        ]
        if x
    )
    rows: list[Form4Transaction] = []
    for txn in root.findall(".//nonDerivativeTransaction"):
        shares = _number(_text(txn, ".//transactionAmounts/transactionShares/value"))
        price = _number(_text(txn, ".//transactionAmounts/transactionPricePerShare/value"))
        transaction_code = _text(txn, ".//transactionCoding/transactionCode")
        acquired_disposed = _text(
            txn, ".//transactionAmounts/transactionAcquiredDisposedCode/value"
        )
        # Filter before model construction because excluded grants often have no price.
        if (
            transaction_code.upper() != "P"
            or acquired_disposed.upper() != "A"
            or shares <= 0
            or price <= 0
        ):
            continue
        record = Form4Transaction(
            issuer_cik=issuer_cik,
            ticker=ticker,
            insider_name=insider_name,
            reporting_owner_cik=reporting_owner_cik,
            report_date=report_date,
            insider_role=roles,
            is_officer=is_officer,
            is_director=is_director,
            is_ten_percent_owner=is_ten,
            transaction_date=date.fromisoformat(_text(txn, ".//transactionDate/value")),
            filing_date=filing_date,
            transaction_code=transaction_code,
            acquired_disposed=acquired_disposed,
            shares=shares,
            price_per_share=price,
            transaction_value=shares * price,
            ownership_nature=_text(txn, ".//ownershipNature/directOrIndirectOwnership/value"),
            accession_number=accession_number,
            is_amendment=is_amendment,
        )
        if record.is_qualifying_purchase:
            rows.append(record)
    return rows


def parse_form4_filing(
    payload: bytes, *, filing_date: date, accession_number: str, is_amendment: bool = False
) -> Form4Filing:
    """Parse retained filing metadata and any qualifying purchases.

    Metadata is deliberately returned for amendments with no qualifying rows so
    callers can remove the superseded filing from point-in-time features.
    """
    root = ET.fromstring(payload)
    for element in root.iter():
        element.tag = element.tag.rsplit("}", 1)[-1]
    issuer_cik = _text(root, ".//issuer/issuerCik")
    owners = root.findall(".//reportingOwner")
    owner_ciks = tuple(_text(owner, ".//reportingOwnerId/rptOwnerCik") for owner in owners)
    report_date_text = _text(root, ".//periodOfReport")
    report_date = date.fromisoformat(report_date_text) if report_date_text else filing_date
    return Form4Filing(
        issuer_cik=issuer_cik,
        reporting_owner_ciks=owner_ciks,
        report_date=report_date,
        filing_date=filing_date,
        accession_number=accession_number,
        is_amendment=is_amendment,
        is_joint_filing=len(owners) != 1,
        transactions=parse_form4_xml(
            payload,
            filing_date=filing_date,
            accession_number=accession_number,
            is_amendment=is_amendment,
        ),
    )


def deduplicate_form4_transactions(
    rows: list[Form4Transaction], as_of: date, filings: list[Form4Filing] | None = None
) -> list[Form4Transaction]:
    """Retain purchases from the latest visible filing for each reporting key.

    The latest filing is chosen by filing date then accession number for each
    issuer/reporting-owner-CIK/report-date key.  Filing metadata is considered
    even when an amendment has no qualifying purchase rows, allowing a later
    nonqualifying Form 4/A to remove all earlier purchase evidence.
    """
    visible = [row for row in rows if row.filing_date <= as_of and row.is_qualifying_purchase]
    latest_accession: dict[tuple[str, str, date], tuple[date, str]] = {}

    def consider(key: tuple[str, str, date] | None, filed: date, accession: str) -> None:
        if key is None:
            return
        current = latest_accession.get(key)
        candidate = (filed, accession)
        if current is None or candidate > current:
            latest_accession[key] = candidate

    # Rows provide fallback metadata for callers that only hold transactions.
    for row in visible:
        consider(row.amendment_filing_key, row.filing_date, row.accession_number)
    for filing in filings or []:
        if filing.filing_date <= as_of:
            consider(filing.amendment_filing_key, filing.filing_date, filing.accession_number)

    retained = [
        row
        for row in visible
        if latest_accession.get(row.amendment_filing_key) == (row.filing_date, row.accession_number)
    ]
    latest: dict[tuple[str, str, date, str, float, float], Form4Transaction] = {}
    for row in retained:
        old = latest.get(row.purchase_event_key)
        if old is None or (row.filing_date, row.accession_number) > (
            old.filing_date,
            old.accession_number,
        ):
            latest[row.purchase_event_key] = row
    return list(latest.values())


def form4_rolling_features(
    rows: list[Form4Transaction],
    *,
    as_of: date,
    market_cap_usd: float,
    filings: list[Form4Filing] | None = None,
) -> dict[str, float | int | bool]:
    """Create point-in-time features from qualifying purchases available by *as_of*."""
    visible = deduplicate_form4_transactions(rows, as_of, filings)
    by_window = {
        days: [r for r in visible if 0 <= (as_of - r.transaction_date).days <= days]
        for days in (7, 14, 30)
    }
    recent = by_window[30]
    values: dict[str, float | int | bool] = {}
    for days, window in by_window.items():
        values[f"form4_unique_insiders_buying_{days}d"] = len(
            {r.reporting_owner_cik for r in window}
        )
    total = sum(r.transaction_value for r in recent)
    triggered = by_window[7]
    triggered_total = sum(r.transaction_value for r in triggered)
    values.update(
        {
            "form4_aggregate_purchase_value_7d": triggered_total,
            "form4_purchase_value_market_cap_ratio_7d": triggered_total / market_cap_usd
            if market_cap_usd > 0
            else 0.0,
            "form4_aggregate_purchase_value_30d": total,
            "form4_purchase_value_market_cap_ratio_30d": total / market_cap_usd
            if market_cap_usd > 0
            else 0.0,
            "form4_largest_individual_purchase_7d": max(
                (r.transaction_value for r in triggered), default=0.0
            ),
            "form4_largest_individual_purchase_30d": max(
                (r.transaction_value for r in recent), default=0.0
            ),
            "form4_officer_director_10pct_participants_7d": len(
                {
                    r.reporting_owner_cik
                    for r in triggered
                    if r.is_officer or r.is_director or r.is_ten_percent_owner
                }
            ),
            "form4_officer_director_10pct_participants_30d": len(
                {
                    r.reporting_owner_cik
                    for r in recent
                    if r.is_officer or r.is_director or r.is_ten_percent_owner
                }
            ),
            "form4_repeated_purchase_insiders_7d": sum(
                1
                for grouped in _group_by_insider(triggered).values()
                if len({(row.transaction_date, row.accession_number) for row in grouped}) > 1
            ),
            "form4_repeated_purchase_insiders_30d": sum(
                1
                for grouped in _group_by_insider(recent).values()
                if len({(row.transaction_date, row.accession_number) for row in grouped}) > 1
            ),
            "form4_filing_age_days": min(
                (max(0, (as_of - r.filing_date).days) for r in by_window[7]), default=999
            ),
            "form4_cluster_buying_7d": len({r.reporting_owner_cik for r in by_window[7]}) >= 3,
        }
    )
    return values


def _group_by_insider(rows: list[Form4Transaction]) -> dict[str, list[Form4Transaction]]:
    grouped: dict[str, list[Form4Transaction]] = defaultdict(list)
    for row in rows:
        grouped[row.reporting_owner_cik].append(row)
    return grouped
