"""The complete local screening → report → thesis → monitoring workflow."""

from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path

import typer

from smct_research.core.models import normalize_utc
from smct_research.research.builder import build_report, report_id
from smct_research.research.changes import compare_runs, render_changes
from smct_research.research.render import render_report, render_screen
from smct_research.research.store import ResearchStore
from smct_research.research.thesis import ThesisDocument, draft_thesis, review_thesis
from smct_research.screening.configuration import load_settings
from smct_research.screening.io import load_feature_snapshots, load_universe, write_csv, write_json
from smct_research.screening.registry import default_registry
from smct_research.screening.service import BatchEvaluationService

DEFAULT_DB = Path(".smct/research.sqlite3")
thesis_app = typer.Typer(no_args_is_help=True, help="Save and review versioned investment theses.")


def parse_time(value: str | None) -> datetime:
    return (
        normalize_utc(datetime.fromisoformat(value.replace("Z", "+00:00")))
        if value
        else datetime.now(UTC)
    )


def write_text(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")


def research(
    universe_file: Path,
    features_directory: Path,
    output_dir: Path = typer.Option(Path("research-output")),  # noqa: B008
    db: Path = typer.Option(DEFAULT_DB),  # noqa: B008
    as_of: str | None = typer.Option(None),
    top: int | None = typer.Option(None, min=1),  # noqa: B008
    min_score: float = typer.Option(0, min=0, max=100),
    min_coverage: float = typer.Option(0, min=0, max=100),
    include_ineligible: bool = typer.Option(False),
    config: Path | None = typer.Option(None),  # noqa: B008
) -> None:
    """Screen companies, produce readable reports and save a reproducible research run."""
    try:
        timestamp = parse_time(as_of)
        policy, scorer = load_settings(config)
        companies = load_universe(universe_file)
        snapshots = load_feature_snapshots(features_directory)
        registry = default_registry()
        # Store the complete universe. Display filters must not change comparison history.
        results = BatchEvaluationService(registry, scorer).evaluate(
            companies, snapshots, policy, timestamp, include_ineligible=True
        )
        company_by_ticker = {company.ticker: company for company in companies}
        reports = [
            build_report(
                company_by_ticker[row.ticker], snapshots.get(row.ticker), row, registry, scorer
            )
            for row in results
        ]
        with ResearchStore(db) as store:
            run_id = store.save_run(reports, timestamp, policy.model_dump(mode="json"))
        selected = [
            row
            for row in results
            if (row.universe_eligible or include_ineligible)
            and row.feature_completeness_percentage >= min_coverage
            and (
                min_score == 0
                or (row.composite_score is not None and row.composite_score >= min_score)
            )
        ]
        if top:
            selected = selected[:top]
        by_ticker = {report.company.ticker: report for report in reports}
        selected_reports = [by_ticker[row.ticker] for row in selected]
        output_dir.mkdir(parents=True, exist_ok=True)
        write_json(output_dir / "ranked.json", selected)
        write_csv(output_dir / "ranked.csv", selected)
        links = []
        for report in selected_reports:
            safe = re.sub(r"[^A-Za-z0-9._-]", "_", report.company.ticker)
            filename = f"{safe}-{report_id(report)[:12]}"
            write_text(output_dir / "companies" / f"{filename}.md", render_report(report))
            write_text(
                output_dir / "companies" / f"{filename}.json",
                report.model_dump_json(indent=2) + "\n",
            )
            links.append(f"- [{report.company.ticker}](companies/{filename}.md)")
        write_text(
            output_dir / "index.md",
            render_screen(selected_reports) + "\n## Company reports\n\n" + "\n".join(links) + "\n",
        )
        write_text(
            output_dir / "manifest.json",
            json.dumps(
                {
                    "run_id": run_id,
                    "as_of": timestamp.isoformat(),
                    "total_companies": len(reports),
                    "displayed_companies": len(selected_reports),
                    "report_ids": [report_id(report) for report in selected_reports],
                },
                indent=2,
            )
            + "\n",
        )
    except (OSError, ValueError, TypeError, KeyError) as error:
        raise typer.BadParameter(str(error)) from error
    typer.echo(f"Saved run {run_id}: {len(reports)} companies; {len(selected_reports)} displayed.")
    typer.echo(f"Open {output_dir / 'index.md'}")


def report(
    ticker: str,
    db: Path = typer.Option(DEFAULT_DB),  # noqa: B008
    as_of: str | None = typer.Option(None),
    output: Path | None = typer.Option(None),  # noqa: B008
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Read a saved company report, optionally at an earlier point in time."""
    try:
        with ResearchStore(db) as store:
            value = store.report(ticker, parse_time(as_of) if as_of else None)
        text = value.model_dump_json(indent=2) + "\n" if json_output else render_report(value)
        if output:
            write_text(output, text)
        else:
            typer.echo(text)
    except (OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error


@thesis_app.command("init")
def thesis_init(ticker: str, output: Path, db: Path = typer.Option(DEFAULT_DB)) -> None:  # noqa: B008
    """Create an editable draft from a saved report; no investment thesis is invented."""
    try:
        if output.exists():
            raise ValueError("Output already exists; choose a new draft filename")
        with ResearchStore(db) as store:
            value = draft_thesis(store.report(ticker))
        write_text(output, value.model_dump_json(indent=2) + "\n")
        typer.echo(f"Draft written to {output}. Edit it, then run smct thesis save {output}.")
    except (OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error


@thesis_app.command("save")
def thesis_save(path: Path, db: Path = typer.Option(DEFAULT_DB)) -> None:  # noqa: B008
    """Append a thesis revision and update the input file's revision number."""
    try:
        value = ThesisDocument.model_validate_json(path.read_text(encoding="utf-8"))
        with ResearchStore(db) as store:
            saved = store.save_thesis(value)
        write_text(path, saved.model_dump_json(indent=2) + "\n")
        typer.echo(f"Saved {saved.ticker} revision {saved.revision} ({saved.status.value}).")
    except (OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error


@thesis_app.command("show")
def thesis_show(
    ticker: str,
    db: Path = typer.Option(DEFAULT_DB),  # noqa: B008
    revision: int | None = typer.Option(None, min=1),  # noqa: B008
    output: Path | None = typer.Option(None),  # noqa: B008
) -> None:  # noqa: B008
    """Read current or historical thesis content, or export it for editing."""
    try:
        with ResearchStore(db) as store:
            value = store.thesis(ticker, revision)
        text = value.model_dump_json(indent=2) + "\n"
        if output:
            write_text(output, text)
        else:
            typer.echo(text)
    except (OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error


@thesis_app.command("list")
def thesis_list(db: Path = typer.Option(DEFAULT_DB)) -> None:  # noqa: B008
    """List the saved watchlist and thesis status."""
    with ResearchStore(db) as store:
        items = store.theses()
    for item in items:
        typer.echo(
            f"{item.ticker:12} {item.status.value:16} revision {item.revision}  {item.title}"
        )
    if not items:
        typer.echo("No saved theses. Run smct thesis init after a research screen.")


@thesis_app.command("review")
def thesis_review(
    features_directory: Path,
    db: Path = typer.Option(DEFAULT_DB),  # noqa: B008
    as_of: str | None = typer.Option(None),
    output: Path | None = typer.Option(None),  # noqa: B008
) -> None:  # noqa: B008
    """Flag invalidation rules and due catalysts without changing thesis status."""
    try:
        snapshots = load_feature_snapshots(features_directory)
        timestamp = parse_time(as_of)
        with ResearchStore(db) as store:
            reviews = [
                review_thesis(item, snapshots.get(item.ticker), timestamp)
                for item in store.theses()
            ]
        text = json.dumps(reviews, indent=2, allow_nan=False) + "\n"
        if output:
            write_text(output, text)
        else:
            typer.echo(text)
    except (OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error


def changes(
    since: str = typer.Option(...),
    as_of: str | None = typer.Option(None),
    db: Path = typer.Option(DEFAULT_DB),  # noqa: B008
    watchlist_only: bool = typer.Option(False),
    output: Path | None = typer.Option(None),  # noqa: B008
    json_output: bool = typer.Option(False, "--json"),
) -> None:
    """Compare two complete saved runs; --since selects the latest run on/before that time."""
    try:
        with ResearchStore(db) as store:
            before = store.run(parse_time(since))
            after = store.run(parse_time(as_of) if as_of else None)
            tickers = {item.ticker for item in store.theses()} if watchlist_only else None
        value = compare_runs(before, after, tickers)
        text = json.dumps(value, indent=2) + "\n" if json_output else render_changes(value)
        if output:
            write_text(output, text)
        else:
            typer.echo(text)
    except (OSError, ValueError) as error:
        raise typer.BadParameter(str(error)) from error


def register_workflow_commands(app: typer.Typer) -> None:
    app.command()(research)
    app.command()(report)
    app.command()(changes)
    app.add_typer(thesis_app, name="thesis")
