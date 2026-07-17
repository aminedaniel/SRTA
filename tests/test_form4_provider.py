import json
from pathlib import Path

from smct_research.providers.sec_edgar import SecEdgarProvider


class NoWait:
    def acquire(self) -> None:
        pass


def test_provider_ingests_current_and_archived_form4_filings_end_to_end(tmp_path: Path) -> None:
    xml = (Path("tests/fixtures/sec/form4_purchase.xml")).read_bytes()
    current = {
        "filings": {
            "recent": {
                "form": ["4"],
                "accessionNumber": ["0001-01"],
                "filingDate": ["2026-07-11"],
                "primaryDocument": ["current.xml"],
            },
            "files": [{"name": "CIK0000000123-submissions-001.json"}],
        }
    }
    archived = {
        "form": ["4/A"],
        "accessionNumber": ["0001-02"],
        "filingDate": ["2026-07-12"],
        "primaryDocument": ["archived.xml"],
    }

    def transport(url: str, headers: dict[str, str]) -> bytes:
        if url.endswith("CIK0000000123.json"):
            return json.dumps(current).encode()
        if url.endswith("CIK0000000123-submissions-001.json"):
            return json.dumps(archived).encode()
        if url.endswith("current.xml") or url.endswith("archived.xml"):
            return xml
        raise AssertionError(url)

    provider = SecEdgarProvider(
        "tests test@example.com", tmp_path, transport=transport, rate_limiter=NoWait()
    )
    filings = provider.form4_filings("123")
    assert [filing["form"] for filing in filings] == ["4", "4/A"]
    records = provider.form4_transactions("123")
    assert len(records) == 2
    assert records[0].reporting_owner_cik == "789"
    assert records[1].is_amendment is True
