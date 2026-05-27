"""CLI entry point for cv-sender."""

from __future__ import annotations

import shutil
from pathlib import Path

import typer
from rich import print as rprint
from rich.table import Table

app = typer.Typer(
    name="cv-sender",
    help="Local job application assistant. Fills forms – never auto-submits.",
    add_completion=False,
)

_EXAMPLE_DIR = Path(__file__).parent.parent.parent / "config"
_CONFIG_EXAMPLES = {
    "config/profile.yaml": "config/profile.example.yaml",
    "config/settings.yaml": "config/settings.example.yaml",
}
_DATA_EXAMPLES = {
    "data/offers.json": "data/offers.example.json",
    "data/applications.json": "data/applications.example.json",
}


# ---------------------------------------------------------------------------
# init
# ---------------------------------------------------------------------------


@app.command()
def init() -> None:
    """Create local config and data files from examples (if they don't exist)."""
    _ensure_dir("config")
    _ensure_dir("data")

    all_examples = {**_CONFIG_EXAMPLES, **_DATA_EXAMPLES}
    for dest_str, src_str in all_examples.items():
        dest = Path(dest_str)
        src = Path(src_str)
        if dest.exists():
            rprint(f"[yellow]Skipping[/yellow] {dest} (already exists)")
            continue
        if src.exists():
            shutil.copy(src, dest)
            rprint(f"[green]Created[/green] {dest}")
        else:
            dest.touch()
            rprint(f"[green]Created[/green] {dest} (empty)")

    rprint("\n[bold]Done![/bold] Edit [cyan]config/profile.yaml[/cyan] and [cyan]config/settings.yaml[/cyan] before proceeding.")


# ---------------------------------------------------------------------------
# score-offers
# ---------------------------------------------------------------------------


@app.command(name="score-offers")
def score_offers(
    use_llm: bool = typer.Option(True, "--use-llm/--no-llm", help="Enable LM Studio scoring"),
) -> None:
    """Score all offers in data/offers.json and save the results."""
    from cv_sender.config import load_settings
    from cv_sender.llm import get_llm_score
    from cv_sender.scorer import score_offer
    from cv_sender.storage import load_offers, save_offers

    settings = load_settings()
    offers = load_offers()

    if not offers:
        rprint("[yellow]No offers found.[/yellow] Add some with [cyan]cv-sender add-offer[/cyan].")
        return

    scored = []
    for offer in offers:
        llm_result = None
        if use_llm and settings.lm_studio.enabled:
            llm_result = get_llm_score(
                offer_data=offer.model_dump(mode="json"),
                criteria_data=settings.model_dump(mode="json"),
                config=settings.lm_studio,
            )
        updated = score_offer(offer, settings, llm_result)
        scored.append(updated)
        rprint(
            f"[cyan]{updated.title}[/cyan] @ {updated.company} → "
            f"score=[bold]{updated.score}[/bold] decision=[bold]{updated.decision}[/bold]"
        )

    save_offers(scored)
    rprint(f"\n[green]Scored {len(scored)} offers.[/green]")


# ---------------------------------------------------------------------------
# list
# ---------------------------------------------------------------------------


@app.command(name="list")
def list_items(
    show: str = typer.Option("offers", "--show", help="What to list: offers | applications"),
    status: str | None = typer.Option(None, "--status", help="Filter applications by status"),
    decision: str | None = typer.Option(None, "--decision", help="Filter offers by decision"),
) -> None:
    """List offers or applications."""
    if show == "applications":
        _list_applications(status_filter=status)
    else:
        _list_offers(decision_filter=decision)


def _list_offers(decision_filter: str | None = None) -> None:
    from cv_sender.storage import load_offers

    offers = load_offers()
    if decision_filter:
        offers = [o for o in offers if str(o.decision) == decision_filter]

    table = Table(title=f"Offers ({len(offers)})")
    table.add_column("ID", style="dim", max_width=12)
    table.add_column("Title")
    table.add_column("Company")
    table.add_column("Salary", justify="right")
    table.add_column("Score", justify="right")
    table.add_column("Decision")

    for o in offers:
        salary = f"{o.salary_min}–{o.salary_max} {o.currency}" if o.salary_min else "—"
        table.add_row(
            o.id[:8],
            o.title,
            o.company,
            salary,
            str(o.score) if o.score is not None else "—",
            str(o.decision or "—"),
        )
    rprint(table)


def _list_applications(status_filter: str | None = None) -> None:
    from cv_sender.storage import load_applications

    apps = load_applications()
    if status_filter:
        apps = [a for a in apps if a.status == status_filter]

    table = Table(title=f"Applications ({len(apps)})")
    table.add_column("ID", style="dim", max_width=12)
    table.add_column("Title")
    table.add_column("Company")
    table.add_column("Status")
    table.add_column("Score", justify="right")
    table.add_column("Created")

    for a in apps:
        table.add_row(
            a.id[:8],
            a.title,
            a.company,
            a.status,
            str(a.score) if a.score is not None else "—",
            str(a.created_at.date()),
        )
    rprint(table)


# ---------------------------------------------------------------------------
# apply
# ---------------------------------------------------------------------------


@app.command()
def apply(
    offer_id: str = typer.Option(..., "--offer-id", help="ID of the offer to apply for"),
) -> None:
    """Open the offer page, fill the application form, and wait for manual review."""
    from datetime import UTC, datetime

    from cv_sender.config import load_profile, load_settings
    from cv_sender.form_filler import fill_application
    from cv_sender.models import Application, ApplicationEvent, ApplicationStatus
    from cv_sender.storage import add_application, get_offer_by_id, update_offer

    settings = load_settings()
    profile = load_profile()

    offer = get_offer_by_id(offer_id)
    if offer is None:
        rprint(f"[red]Offer '{offer_id}' not found.[/red]")
        raise typer.Exit(1)

    rprint(f"Opening [cyan]{offer.title}[/cyan] at [cyan]{offer.company}[/cyan]…")

    try:
        fill_application(offer, profile, settings)
    except Exception as exc:  # noqa: BLE001
        rprint(f"[red]Browser error:[/red] {exc}")
        raise typer.Exit(1) from exc

    # Record the application
    application = Application(
        offer_id=offer.id,
        source=offer.source,
        url=offer.url,
        company=offer.company,
        title=offer.title,
        salary_min=offer.salary_min,
        salary_max=offer.salary_max,
        currency=offer.currency,
        contract=offer.contract,
        location=offer.location,
        status=ApplicationStatus.READY_TO_SEND,
        score=offer.score,
        cv_file=profile.cv_path,
        events=[
            ApplicationEvent(
                event="form_filled",
                details="Form filled via cv-sender; awaiting manual submission",
            )
        ],
    )
    add_application(application)

    # Update offer status
    updated_offer = offer.model_copy(update={"updated_at": datetime.now(UTC)} if hasattr(offer, "updated_at") else {})
    update_offer(updated_offer)

    rprint(f"[green]Application recorded[/green] (id={application.id[:8]})")


# ---------------------------------------------------------------------------
# add-offer
# ---------------------------------------------------------------------------


@app.command(name="add-offer")
def add_offer_cmd() -> None:
    """Add a job offer manually via an interactive prompt."""
    from cv_sender.models import Offer
    from cv_sender.storage import add_offer

    rprint("[bold]Add a new job offer[/bold]")

    url = typer.prompt("Offer URL")
    title = typer.prompt("Job title")
    company = typer.prompt("Company name", default="")
    source = typer.prompt("Source (e.g. rocketjobs, pracuj, linkedin)", default="manual")
    location = typer.prompt("Location", default="")
    contract = typer.prompt("Contract type (B2B / UoP / other)", default="")
    salary_min_str = typer.prompt("Salary min (leave blank if unknown)", default="")
    salary_max_str = typer.prompt("Salary max (leave blank if unknown)", default="")
    currency = typer.prompt("Currency", default="PLN")
    technologies_str = typer.prompt("Technologies (comma-separated)", default="")
    description = typer.prompt("Short description", default="")

    salary_min = float(salary_min_str) if salary_min_str.strip() else None
    salary_max = float(salary_max_str) if salary_max_str.strip() else None
    technologies = [t.strip() for t in technologies_str.split(",") if t.strip()]

    offer = Offer(
        url=url,
        title=title,
        company=company,
        source=source,
        location=location,
        contract=contract,
        salary_min=salary_min,
        salary_max=salary_max,
        currency=currency,
        technologies=technologies,
        description=description,
    )

    saved = add_offer(offer)
    if saved:
        rprint(f"[green]Offer saved[/green] (id={offer.id[:8]})")
    else:
        rprint("[yellow]Offer with this URL already exists – skipped.[/yellow]")


# ---------------------------------------------------------------------------
# ui
# ---------------------------------------------------------------------------


@app.command()
def ui(
    host: str = typer.Option("localhost", "--host", help="Hostname for the Streamlit server"),
    port: int = typer.Option(8501, "--port", help="Port for the Streamlit server"),
) -> None:
    """Launch the Streamlit web UI."""
    import subprocess
    import sys
    from pathlib import Path

    ui_path = Path(__file__).parent / "ui.py"
    cmd = [
        sys.executable,
        "-m",
        "streamlit",
        "run",
        str(ui_path),
        "--server.address",
        host,
        "--server.port",
        str(port),
    ]
    rprint(f"[bold green]Starting cv-sender UI[/bold green] → http://{host}:{port}")
    try:
        subprocess.run(cmd, check=True)
    except KeyboardInterrupt:
        pass
    except subprocess.CalledProcessError as exc:
        rprint(f"[red]Streamlit exited with code {exc.returncode}[/red]")
        raise typer.Exit(exc.returncode) from exc


# ---------------------------------------------------------------------------
# bookmarklet-server
# ---------------------------------------------------------------------------


@app.command(name="bookmarklet-server")
def bookmarklet_server(
    host: str = typer.Option("127.0.0.1", "--host", help="Bind address (keep 127.0.0.1 for local-only)"),
    port: int = typer.Option(8765, "--port", help="Port for the bookmarklet receiver"),
) -> None:
    """Run the local bookmarklet import server on http://127.0.0.1:8765.

    Keep this server running while you browse job boards.
    Clicking the bookmarklet in your browser will open a new tab that calls
    the /import endpoint and shows the result.
    """
    import uvicorn

    from cv_sender.bookmarklet_server import BOOKMARKLET_JS, app as bm_app

    rprint(f"[bold green]Starting bookmarklet server[/bold green] → http://{host}:{port}")
    rprint(f"\n[bold]Bookmarklet JavaScript:[/bold]\n[cyan]{BOOKMARKLET_JS}[/cyan]\n")
    rprint("Copy the line above as the URL of a browser bookmark named [bold]Save to Job Assistant[/bold].")
    rprint("Press [bold]Ctrl-C[/bold] to stop.\n")

    uvicorn.run(bm_app, host=host, port=port, log_level="info")


# ---------------------------------------------------------------------------
# collect-jobs
# ---------------------------------------------------------------------------


@app.command(name="collect-jobs")
def collect_jobs(
    sources: list[str] = typer.Option(
        [],
        "--source",
        "-s",
        help="Sources to collect from. Repeatable. Defaults to all enabled in config.",
    ),
    emergency: bool = typer.Option(
        False,
        "--emergency",
        help="Use emergency React/Frontend preset criteria.",
    ),
    no_score: bool = typer.Option(False, "--no-score", help="Skip LLM scoring after import."),
) -> None:
    """Collect job offers from job boards and import them."""
    from cv_sender.collectors.base import JobSearchCriteria
    from cv_sender.config import load_settings
    from cv_sender.job_search import run_job_collection

    settings = load_settings()
    cfg = settings.job_search

    if emergency:
        criteria = JobSearchCriteria.emergency_react()
        rprint("[bold yellow]Emergency React/Frontend mode active.[/bold yellow]")
    else:
        criteria = JobSearchCriteria.from_config(cfg)

    if sources:
        active_sources = list(sources)
    else:
        active_sources = [n for n, s in cfg.sources.items() if s.enabled]

    if not active_sources:
        rprint("[yellow]No sources enabled. Pass --source or enable sources in settings.[/yellow]")
        raise typer.Exit(1)

    rprint(f"Collecting from: {', '.join(active_sources)}")
    results = run_job_collection(criteria, active_sources, auto_score=not no_score)

    table = Table(title="Collection results")
    table.add_column("Source")
    table.add_column("Collected", justify="right")
    table.add_column("Imported", justify="right")
    table.add_column("Duplicate", justify="right")
    table.add_column("Skipped", justify="right")
    table.add_column("Failed", justify="right")

    for r in results:
        table.add_row(
            r.source,
            str(r.collected_count),
            str(r.imported_count),
            str(r.duplicate_count),
            str(r.skipped_count),
            str(r.failed_count),
        )
        for err in r.errors:
            rprint(f"  [red]Error ({r.source}):[/red] {err}")

    rprint(table)


# ---------------------------------------------------------------------------
# build-queue
# ---------------------------------------------------------------------------


@app.command(name="build-queue")
def build_queue() -> None:
    """Build / refresh the rapid-apply queue from scored offers."""
    from cv_sender.apply_queue import build_apply_queue_from_offers, get_queue_stats

    rprint("Building apply queue …")
    build_apply_queue_from_offers()
    stats = get_queue_stats()
    rprint("[bold green]Queue built.[/bold green]")
    for status, count in stats.items():
        rprint(f"  {status}: {count}")


# ---------------------------------------------------------------------------
# fill-next
# ---------------------------------------------------------------------------


@app.command(name="fill-next")
def fill_next_queued() -> None:
    """Fill the form for the next queued offer (never auto-submits)."""
    from cv_sender.apply_queue import get_next_queue_item, mark_queue_item_status
    from cv_sender.models import ApplyQueueItemStatus

    item = get_next_queue_item()
    if item is None:
        rprint("[yellow]No items in the queue. Run [bold]build-queue[/bold] first.[/yellow]")
        raise typer.Exit(0)

    rprint(
        f"Next: [bold]{item.title}[/bold] @ {item.company}  "
        f"(score={item.score or 'N/A'}, priority={item.priority_score:.1f})"
    )
    rprint(f"URL: {item.url}")

    try:
        from cv_sender import services  # noqa: PLC0415
        from cv_sender.storage import get_offer_by_id  # noqa: PLC0415

        offer = get_offer_by_id(item.offer_id)
        if offer is None:
            rprint(f"[red]Offer {item.offer_id!r} not found in storage.[/red]")
            raise typer.Exit(1)

        mark_queue_item_status(item.id, ApplyQueueItemStatus.IN_PROGRESS)
        # fill_form enforces auto_submit=False by design
        result = services.fill_form(offer_id=item.offer_id, auto_submit=False)
        rprint(f"Fill result: {result.status}")
        if result.message:
            rprint(result.message)

        if result.status.value in ("success", "filled"):
            mark_queue_item_status(item.id, ApplyQueueItemStatus.FILLED)
        else:
            mark_queue_item_status(item.id, ApplyQueueItemStatus.FAILED)
    except Exception as exc:  # noqa: BLE001
        rprint(f"[red]Error during fill: {exc}[/red]")
        mark_queue_item_status(item.id, ApplyQueueItemStatus.FAILED)
        raise typer.Exit(1)


# ---------------------------------------------------------------------------
# Helper
# ---------------------------------------------------------------------------


def _ensure_dir(name: str) -> None:
    Path(name).mkdir(parents=True, exist_ok=True)


if __name__ == "__main__":
    app()
