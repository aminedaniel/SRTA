"""Point-in-time and forward-return checks for the research workflow."""

import csv
import json
from datetime import UTC, datetime

import pytest
from typer.testing import CliRunner

from smct_research.cli import app
from smct_research.core.models import FeatureSnapshot
from smct_research.screening.enrichment import enrich
from smct_research.validation import validate_history


def _csv(path, columns, rows):
    with path.open("w", newline="") as handle:
        writer = csv.writer(handle)
        writer.writerow(columns)
        writer.writerows(rows)


def test_enrichment_uses_only_available_prices_and_observations(tmp_path):
    base = {"ALPH": FeatureSnapshot(ticker="ALPH", as_of=datetime(2020, 1, 1, tzinfo=UTC))}
    prices = tmp_path / "prices.csv"
    _csv(
        prices,
        ["ticker", "date", "adjusted_close"],
        [("ALPH", f"2020-01-{day:02}", 10 + day) for day in range(1, 11)],
    )
    evidence = tmp_path / "evidence.csv"
    _csv(
        evidence,
        ["ticker", "feature", "value", "available_at", "source"],
        [
            ("ALPH", "eps_revision_30d", 0.1, "2020-01-04T00:00:00Z", "vendor:file1"),
            ("ALPH", "eps_revision_30d", 0.9, "2020-01-09T00:00:00Z", "vendor:file2"),
        ],
    )
    result = enrich(base, datetime(2020, 1, 5, tzinfo=UTC), prices=prices, evidence=evidence)
    assert result["ALPH"].values["eps_revision_30d"] == 0.1
    assert result["ALPH"].source_as_of["feature:eps_revision_30d"] == datetime(
        2020, 1, 4, tzinfo=UTC
    )
    assert "close" not in result["ALPH"].values  # 200 days of history required
    with pytest.raises(ValueError, match="Future base evidence"):
        enrich(
            {"ALPH": FeatureSnapshot(ticker="ALPH", as_of=datetime(2021, 1, 1, tzinfo=UTC))},
            datetime(2020, 1, 5, tzinfo=UTC),
        )


def test_history_requires_completed_horizons_and_reports_insufficient_alpha(tmp_path):
    universes, snapshots = tmp_path / "universes", tmp_path / "snapshots"
    universes.mkdir()
    cohort_dir = snapshots / "2020-01-01"
    cohort_dir.mkdir(parents=True)
    companies = [
        {
            "ticker": ticker,
            "name": ticker,
            "market_cap_usd": 1e9,
            "sector": "Technology",
            "country": "US",
            "exchange": "NASDAQ",
            "is_active": True,
            "security_type": "Common Stock",
            "average_daily_dollar_volume": 1e7,
        }
        for ticker in ("ALPH", "BETA")
    ]
    (universes / "2020-01-01.json").write_text(json.dumps(companies))
    for ticker in ("ALPH", "BETA"):
        snapshot = FeatureSnapshot(
            ticker=ticker,
            as_of=datetime(2020, 1, 1, tzinfo=UTC),
            values={"yoy_revenue_growth": 0.1, "operating_margin": 0.1},
            source_as_of={"filing": datetime(2019, 12, 31, tzinfo=UTC)},
        )
        (cohort_dir / f"{ticker}.json").write_text(snapshot.model_dump_json())
    prices = tmp_path / "prices.csv"
    _csv(
        prices,
        ["ticker", "date", "adjusted_close"],
        [
            (ticker, day, value)
            for ticker, values in {
                "ALPH": [100, 120, 130],
                "BETA": [100, 110, 120],
                "VOO": [100, 108, 118],
            }.items()
            for day, value in zip(("2020-01-02", "2021-01-02", "2022-01-02"), values, strict=True)
        ],
    )
    result = validate_history(universes, snapshots, prices, coverage=0)
    assert result["summary"]["12"]["complete_cohorts"] == 1
    assert result["summary"]["12"]["alpha_established"] is False
    assert result["summary"]["36"]["complete_cohorts"] == 0
    assert result["cohorts"][0]["net_return"] == pytest.approx(0.145)
    assert (
        validate_history(universes, snapshots, prices)["cohorts"][0]["status"]
        == "no_eligible_picks"
    )
    output = tmp_path / "validation.json"
    invocation = CliRunner().invoke(
        app,
        [
            "validate-history",
            str(universes),
            str(snapshots),
            str(prices),
            "--min-coverage",
            "0",
            "--output-json",
            str(output),
        ],
    )
    assert invocation.exit_code == 0, invocation.output
    assert json.loads(output.read_text())["summary"]["12"]["alpha_established"] is False
