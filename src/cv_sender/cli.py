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
    mode: str | None = typer.Option(
        None,
        "--mode",
        help="Collector mode override: playwright | api | static | hybrid",
    ),
    no_score: bool = typer.Option(False, "--no-score", help="Skip LLM scoring after import."),
) -> None:
    """Collect job offers from job boards and import them."""
    from cv_sender.collectors.base import JobSearchCriteria
    from cv_sender.config import load_settings
    from cv_sender.job_search import collect_jobs as dispatch_collect_jobs

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

    resolved_mode = mode or ("playwright" if emergency else (getattr(cfg, "collector_mode", "playwright") or "playwright"))
    rprint(f"Collecting from: {', '.join(active_sources)}")
    rprint(f"Collector mode: {resolved_mode}")
    report = dispatch_collect_jobs(
        criteria,
        mode=resolved_mode,
        source_names=active_sources,
        auto_score=not no_score,
    )

    table = Table(title="Collection results")
    table.add_column("Source")
    table.add_column("Collector")
    table.add_column("Raw", justify="right")
    table.add_column("Job URLs", justify="right")
    table.add_column("Imported", justify="right")
    table.add_column("Duplicate", justify="right")
    table.add_column("Skipped", justify="right")
    table.add_column("Failed", justify="right")

    for r in report.source_summaries:
        table.add_row(
            r.source,
            r.collector_used or "",
            str(r.raw_found_count),
            str(r.job_offer_url_count or r.found_count),
            str(r.imported_count or r.accepted_count),
            str(r.duplicate_count),
            str(r.skipped_count or r.rejected_count),
            str(r.failed_count),
        )

    for warning in report.global_warnings:
        rprint(f"  [yellow]Warning:[/yellow] {warning}")

    rprint(table)


# ---------------------------------------------------------------------------
# debug-collectors
# ---------------------------------------------------------------------------


@app.command(name="debug-collectors")
def debug_collectors(
    sources: list[str] = typer.Option(
        [],
        "--source",
        "-s",
        help="Sources to test. Repeatable. Defaults to all registered sources.",
    ),
) -> None:
    """Smoke-test each collector with broad React/Frontend criteria.

    Prints per-source diagnostics: HTTP status, raw candidate count, filter results,
    and sample offer previews.  Does not import or store anything.
    """
    import time  # noqa: PLC0415

    from cv_sender.collectors.base import JobSearchCriteria, passes_criteria_filter  # noqa: PLC0415
    from cv_sender.job_search import _get_collector  # noqa: PLC0415

    # Deliberately broad criteria so filter issues are visible.
    broad_criteria = JobSearchCriteria(
        keywords=["React", "Frontend"],
        technologies=["React", "TypeScript"],
        locations=[],
        seniority=[],
        contract_types=[],
        min_salary_b2b=0,
        require_salary=False,
        max_offers_per_source=5,
        max_total_offers=50,
        exclude_keywords=[],
        request_delay_seconds=0.5,
    )

    all_sources = ["justjoin", "rocketjobs", "nofluffjobs", "pracuj", "linkedin"]
    active = list(sources) if sources else all_sources

    rprint("\n[bold]cv-sender debug-collectors[/bold]")
    rprint(f"Broad criteria: keywords={broad_criteria.keywords} technologies={broad_criteria.technologies}")
    rprint(f"Sources: {active}\n")

    for name in active:
        rprint(f"[bold cyan]── {name} ──[/bold cyan]")
        collector = _get_collector(name)
        if collector is None:
            rprint(f"  [red]✗ No collector registered for '{name}'[/red]")
            continue

        rprint(f"  Collector class : {type(collector).__name__}")

        t0 = time.monotonic()
        try:
            raw = collector.search(broad_criteria)
            elapsed = round(time.monotonic() - t0, 2)
        except Exception as exc:  # noqa: BLE001
            elapsed = round(time.monotonic() - t0, 2)
            rprint(f"  [red]✗ search() raised exception after {elapsed}s:[/red] {exc}")
            continue

        raw_count = len(raw)
        passed = [o for o in raw if not passes_criteria_filter(o, broad_criteria)]
        rejected = raw_count - len(passed)

        status_icon = "[green]✓[/green]" if raw_count > 0 else "[yellow]⚠[/yellow]"
        rprint(f"  {status_icon} Duration         : {elapsed}s")
        rprint(f"  {status_icon} Raw candidates   : {raw_count}")
        rprint(f"  {'[green]✓[/green]' if passed else '[yellow]⚠[/yellow]'} After filter     : {len(passed)}  (rejected by filter: {rejected})")

        if raw_count == 0:
            rprint("  [yellow]⚠ 0 raw results — check the API endpoint, network, or source availability.[/yellow]")
        elif len(passed) == 0:
            rprint("  [yellow]⚠ raw > 0 but all rejected — filters may be too strict for this source.[/yellow]")

        # Show first 3 raw sample previews.
        for i, offer in enumerate(raw[:3]):
            skip = passes_criteria_filter(offer, broad_criteria)
            flag = "[green]pass[/green]" if not skip else f"[yellow]skip: {skip[:60]}[/yellow]"
            title = (offer.title or "(no title)")[:60]
            company = (offer.company or "")[:30]
            rprint(f"    [{i+1}] {title} @ {company} — {flag}")

        rprint("")

    rprint("[dim]Note: debug-collectors does not save or import any offers.[/dim]")


# ---------------------------------------------------------------------------
# collect-playwright
# ---------------------------------------------------------------------------


@app.command(name="collect-playwright")
def collect_playwright(
    sources: list[str] = typer.Option(
        [],
        "--source",
        "-s",
        help="Sources to collect from. Repeatable. Defaults to: justjoin rocketjobs nofluffjobs pracuj.",
    ),
    listing_url: list[str] = typer.Option(
        [],
        "--url",
        "-u",
        help="Custom listing URL(s). Applies to the FIRST --source value when multiple sources given.",
    ),
    emergency: bool = typer.Option(False, "--emergency", help="Use emergency React/Frontend criteria."),
    headless: bool = typer.Option(False, "--headless", help="Run browser in headless mode."),
    max_urls: int = typer.Option(0, "--max-urls", help="Max URLs per source (0 = use config value)."),
    no_import: bool = typer.Option(False, "--no-import", help="Collect URLs but do not import."),
    no_score: bool = typer.Option(False, "--no-score", help="Skip LLM scoring after import."),
) -> None:
    """Collect job-offer URLs using a real browser (Playwright) and optionally import them."""
    from cv_sender.collectors.base import JobSearchCriteria  # noqa: PLC0415
    from cv_sender.config import PlaywrightCollectionConfig, load_settings  # noqa: PLC0415
    from cv_sender.playwright_collection import collect_and_import, collect_job_urls  # noqa: PLC0415

    settings = load_settings()
    pw_cfg = settings.playwright_collection

    if emergency:
        criteria = JobSearchCriteria.emergency_react()
        rprint("[bold yellow]Emergency React/Frontend mode active.[/bold yellow]")
    else:
        criteria = JobSearchCriteria.from_config(settings.job_search)

    active_sources = list(sources) if sources else ["justjoin", "rocketjobs", "nofluffjobs", "pracuj"]

    run_cfg = PlaywrightCollectionConfig(
        enabled=True,
        headless=headless or pw_cfg.headless,
        slow_mo_ms=pw_cfg.slow_mo_ms,
        max_scrolls_per_source=pw_cfg.max_scrolls_per_source,
        scroll_pause_ms=pw_cfg.scroll_pause_ms,
        max_urls_per_source=max_urls if max_urls > 0 else pw_cfg.max_urls_per_source,
        save_debug_screenshots=pw_cfg.save_debug_screenshots,
        page_timeout_ms=pw_cfg.page_timeout_ms,
    )

    # Map custom listing URLs to first source when provided
    custom_map: dict[str, list[str]] = {}
    if listing_url and active_sources:
        custom_map[active_sources[0]] = list(listing_url)

    rprint(f"Playwright collecting from: {', '.join(active_sources)}")
    rprint(f"headless={run_cfg.headless}  max_urls={run_cfg.max_urls_per_source}  import={not no_import}")

    if no_import:
        results = collect_job_urls(criteria, active_sources, run_cfg, custom_map or None)
        all_urls = [cu.url for r in results for cu in r.collected_urls]
        rprint(f"\n[bold green]Collected {len(all_urls)} URLs (not imported).[/bold green]")
        for u in all_urls[:20]:
            rprint(f"  {u}")
        if len(all_urls) > 20:
            rprint(f"  … and {len(all_urls) - 20} more")
    else:
        summary = collect_and_import(
            criteria, active_sources, run_cfg,
            auto_score=not no_score,
            custom_listing_urls=custom_map or None,
        )
        rprint(
            f"\n[bold green]Done.[/bold green] "
            f"Collected: {summary['total_collected']} · "
            f"Imported: {summary['total_imported']} · "
            f"Duplicates: {summary['total_duplicates']} · "
            f"Failed: {summary['total_failed']}"
        )
        for err in summary.get("errors", []):
            rprint(f"  [red]Error:[/red] {err}")

        table = Table(title="Per-source results")
        table.add_column("Source")
        table.add_column("Listing URLs", justify="right")
        table.add_column("Raw links", justify="right")
        table.add_column("Job URLs", justify="right")
        table.add_column("Duplicates", justify="right")
        table.add_column("Errors", justify="right")
        for r in summary.get("collection_results", []):
            table.add_row(
                r.source,
                str(len(r.listing_urls)),
                str(r.raw_link_count),
                str(r.job_url_count),
                str(r.duplicate_count),
                str(len(r.errors)),
            )
        rprint(table)


# ---------------------------------------------------------------------------
# debug-playwright-collectors
# ---------------------------------------------------------------------------


@app.command(name="debug-playwright-collectors")
def debug_playwright_collectors(
    sources: list[str] = typer.Option(
        [],
        "--source",
        "-s",
        help="Sources to test. Defaults to all four Playwright sources.",
    ),
    headless: bool = typer.Option(False, "--headless", help="Run browser in headless mode."),
) -> None:
    """Smoke-test each Playwright collector.  Opens browser, collects a few URLs, does NOT import."""
    from cv_sender.collectors.base import JobSearchCriteria  # noqa: PLC0415
    from cv_sender.config import PlaywrightCollectionConfig, load_settings  # noqa: PLC0415
    from cv_sender.playwright_collection import collect_job_urls  # noqa: PLC0415

    settings = load_settings()
    pw_cfg = settings.playwright_collection

    broad = JobSearchCriteria(
        keywords=["React", "Frontend"],
        technologies=["React", "TypeScript"],
        locations=[],
        seniority=[],
        contract_types=[],
        min_salary_b2b=0,
        require_salary=False,
        max_offers_per_source=5,
        max_total_offers=50,
        exclude_keywords=[],
        request_delay_seconds=0.5,
    )

    run_cfg = PlaywrightCollectionConfig(
        enabled=True,
        headless=headless or pw_cfg.headless,
        slow_mo_ms=pw_cfg.slow_mo_ms,
        max_scrolls_per_source=2,
        scroll_pause_ms=pw_cfg.scroll_pause_ms,
        max_urls_per_source=5,
        save_debug_screenshots=True,
        page_timeout_ms=pw_cfg.page_timeout_ms,
    )

    active = list(sources) if sources else ["justjoin", "rocketjobs", "nofluffjobs", "pracuj"]
    rprint(f"\n[bold]debug-playwright-collectors[/bold]  sources={active}  headless={run_cfg.headless}")
    results = collect_job_urls(broad, active, run_cfg)

    for r in results:
        status = "[green]✓[/green]" if r.job_url_count > 0 else "[yellow]⚠[/yellow]"
        rprint(f"\n{status} [bold cyan]{r.source}[/bold cyan]")
        rprint(f"  Raw links   : {r.raw_link_count}")
        rprint(f"  Job URLs    : {r.job_url_count}")
        rprint(f"  Duplicates  : {r.duplicate_count}")
        for w in r.warnings:
            rprint(f"  [yellow]⚠ {w}[/yellow]")
        for e in r.errors:
            rprint(f"  [red]✗ {e}[/red]")
        for cu in r.collected_urls[:5]:
            rprint(f"    • {cu.url}")
        if r.debug_artifacts:
            rprint(f"  Debug artifacts saved: {', '.join(r.debug_artifacts[:3])}")

    rprint("\n[dim]Note: debug-playwright-collectors does NOT import any offers.[/dim]")


@app.command(name="debug-playwright-source")
def debug_playwright_source(
    source: str = typer.Option(..., "--source", help="Single source to debug: rocketjobs | justjoin | pracuj | nofluffjobs"),
    keyword: str = typer.Option("React", "--keyword", help="Keyword/query for listing URL generation."),
    headless: str = typer.Option("false", "--headless", help="Run browser in headless mode (true/false)."),
    max_scrolls: int = typer.Option(5, "--max-scrolls", help="Maximum scroll attempts."),
    listing_url: str | None = typer.Option(None, "--listing-url", help="Optional custom listing URL override."),
    save_html: bool = typer.Option(False, "--save-html", help="Save html_preview.html."),
    save_screenshot: bool = typer.Option(True, "--save-screenshot", help="Save initial and post-scroll screenshots."),
    save_trace: bool = typer.Option(False, "--save-trace", help="Save Playwright trace.zip when supported."),
) -> None:
    """Run detailed Playwright debugger for one source without importing offers."""
    from cv_sender.collectors.base import JobSearchCriteria  # noqa: PLC0415
    from cv_sender.config import load_settings  # noqa: PLC0415
    from cv_sender.playwright_collection import debug_collect_source  # noqa: PLC0415

    settings = load_settings()
    base = JobSearchCriteria.from_config(settings.job_search)
    criteria = JobSearchCriteria(
        keywords=[keyword] if keyword else list(base.keywords),
        technologies=list(base.technologies),
        locations=list(base.locations),
        seniority=list(base.seniority),
        contract_types=list(base.contract_types),
        min_salary_b2b=base.min_salary_b2b,
        require_salary=base.require_salary,
        max_offers_per_source=base.max_offers_per_source,
        max_total_offers=base.max_total_offers,
        exclude_keywords=list(base.exclude_keywords),
        request_delay_seconds=base.request_delay_seconds,
    )

    def _parse_bool(value: str) -> bool:
        val = (value or "").strip().lower()
        if val in {"1", "true", "yes", "y", "on"}:
            return True
        if val in {"0", "false", "no", "n", "off", ""}:
            return False
        raise ValueError(f"Invalid boolean value: {value!r}")

    try:
        headless_bool = _parse_bool(headless)
    except ValueError as exc:
        rprint(f"[red]{exc}[/red]")
        raise typer.Exit(1) from exc

    try:
        report = debug_collect_source(
            source=source,
            criteria=criteria,
            listing_url=listing_url,
            headless=headless_bool,
            max_scrolls=max_scrolls,
            save_html=save_html,
            save_screenshot=save_screenshot,
            save_trace=save_trace,
        )
    except Exception as exc:  # noqa: BLE001
        rprint(f"[red]Debug run failed:[/red] {exc}")
        raise typer.Exit(1) from exc

    rprint("\n[bold]Playwright debug complete[/bold]")
    rprint(f"Source: {report.source}")
    rprint(f"Listing URL: {report.listing_url}")
    rprint(f"Final URL: {report.final_url_after_redirect}")
    rprint(f"Page title: {report.page_title}")
    rprint(f"Links before/after scroll: {report.links_before_scroll}/{report.links_after_scroll}")
    rprint(f"Counts: {report.summary_counts}")
    if report.warnings:
        for warning in report.warnings:
            rprint(f"[yellow]Warning:[/yellow] {warning}")
    if report.errors:
        for err in report.errors:
            rprint(f"[red]Error:[/red] {err}")
    rprint(f"Suggested next fix: {report.suggested_next_fix}")
    rprint(f"Debug files: {report.debug_dir}")


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


# ---------------------------------------------------------------------------
# cleanup  (sub-app)
# ---------------------------------------------------------------------------

_cleanup_app = typer.Typer(
    name="cleanup",
    help="Bulk delete offers and dev data.",
    add_completion=False,
)
app.add_typer(_cleanup_app, name="cleanup")


@_cleanup_app.command(name="offers")
def cleanup_offers(
    all_offers: bool = typer.Option(False, "--all", help="Delete ALL offers."),
    source: str = typer.Option("", "--source", "-s", help="Delete offers from this source."),
    dev_only: bool = typer.Option(False, "--dev-only", help="Delete dev/test offers only."),
    score_below: int = typer.Option(0, "--score-below", help="Delete offers with score < N (0 = disabled)."),
    delete_applications: bool = typer.Option(
        False, "--delete-applications", help="Also delete related applications (dangerous)."
    ),
    no_backup: bool = typer.Option(False, "--no-backup", help="Skip backup (not recommended)."),
    yes: bool = typer.Option(False, "--yes", "-y", help="Confirm deletion without prompting."),
) -> None:
    """Delete offers by filter.  Requires --yes to proceed."""
    from cv_sender.cleanup import (  # noqa: PLC0415
        OfferDeleteFilters,
        RelatedCleanupOptions,
        delete_all_offers,
        delete_offers_by_filter,
        preview_offers_by_filter,
    )

    if not any([all_offers, source, dev_only, score_below]):
        rprint("[yellow]Specify at least one of --all, --source, --dev-only, --score-below.[/yellow]")
        raise typer.Exit(1)

    opts = RelatedCleanupOptions(
        delete_queue_items=True,
        delete_quality_reports=True,
        delete_applications=delete_applications,
        delete_debug_runs=False,
    )

    if all_offers:
        from cv_sender.storage import load_offers  # noqa: PLC0415
        count = len(load_offers())
        if not yes:
            rprint(f"[yellow]Would delete ALL {count} offers.[/yellow]")
            rprint("Re-run with [bold]--yes[/bold] to confirm.")
            raise typer.Exit(0)
        with rprint.__module__ and True:  # just a no-op to allow the block
            pass
        result = delete_all_offers(opts, create_backup=not no_backup)
    else:
        filters = OfferDeleteFilters(
            source=source,
            dev_only=dev_only,
            score_below=int(score_below) if score_below > 0 else None,
        )
        matching = preview_offers_by_filter(filters)
        if not matching:
            rprint("[yellow]No offers match the specified filters. Nothing deleted.[/yellow]")
            raise typer.Exit(0)
        if not yes:
            rprint(f"[yellow]Would delete {len(matching)} matching offers:[/yellow]")
            for o in matching[:10]:
                rprint(f"  • {o.get('title', '?')} @ {o.get('company', '?')} [{o.get('source', '?')}]")
            if len(matching) > 10:
                rprint(f"  … and {len(matching) - 10} more")
            rprint("Re-run with [bold]--yes[/bold] to confirm.")
            raise typer.Exit(0)
        result = delete_offers_by_filter(filters, options=opts, create_backup=not no_backup)

    rprint(f"[green]Deleted:[/green] {result.deleted_count}  "
           f"[yellow]Not found:[/yellow] {result.not_found_count}  "
           f"[red]Failed:[/red] {result.failed_count}")
    if result.backup_path:
        rprint(f"Backup: {result.backup_path}")
    for err in result.errors:
        rprint(f"[red]Error:[/red] {err}")


@_cleanup_app.command(name="queue")
def cleanup_queue(
    no_backup: bool = typer.Option(False, "--no-backup"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Clear the entire apply queue."""
    from cv_sender.cleanup import clear_apply_queue  # noqa: PLC0415
    from cv_sender.storage import load_apply_queue  # noqa: PLC0415

    count = len(load_apply_queue())
    if not yes:
        rprint(f"[yellow]Would clear {count} queue item(s).[/yellow]")
        rprint("Re-run with [bold]--yes[/bold] to confirm.")
        raise typer.Exit(0)

    result = clear_apply_queue(create_backup=not no_backup)
    rprint(f"[green]Queue cleared:[/green] {result.deleted_count} item(s) removed.")
    if result.backup_path:
        rprint(f"Backup: {result.backup_path}")


@_cleanup_app.command(name="dev-data")
def cleanup_dev_data(
    delete_applications: bool = typer.Option(
        False, "--delete-applications", help="Also delete related applications (dangerous)."
    ),
    no_backup: bool = typer.Option(False, "--no-backup"),
    yes: bool = typer.Option(False, "--yes", "-y"),
) -> None:
    """Delete dev/test offers, clear queue, clear diagnostics, and optionally debug data."""
    from cv_sender.cleanup import (  # noqa: PLC0415
        OfferDeleteFilters,
        RelatedCleanupOptions,
        dev_cleanup,
        preview_offers_by_filter,
    )

    dev_matches = preview_offers_by_filter(OfferDeleteFilters(dev_only=True))

    if not yes:
        rprint(f"[yellow]Dev cleanup preview:[/yellow]")
        rprint(f"  Dev/test offers to delete : {len(dev_matches)}")
        rprint("  Apply queue               : will be cleared")
        rprint("  Collection diagnostics    : will be cleared")
        rprint("Re-run with [bold]--yes[/bold] to confirm.")
        raise typer.Exit(0)

    opts = RelatedCleanupOptions(
        delete_queue_items=True,
        delete_quality_reports=True,
        delete_applications=delete_applications,
        delete_debug_runs=False,
    )

    results = dev_cleanup(options=opts, create_backup=not no_backup)
    for name, res in results.items():
        rprint(f"  {name}: [green]{res.deleted_count}[/green] deleted"
               + (f"  backup={res.backup_path}" if res.backup_path else ""))


if __name__ == "__main__":
    app()
