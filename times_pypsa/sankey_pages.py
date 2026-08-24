"""Standalone Sankey pages — one per (model year x aggregation level).

``generate_qa_report`` already renders the whole-system Sankey, but it wraps it
in the full QA apparatus: the per-year balance / coverage / ratio CSVs, the
integrity verdict, the fifteen per-category neighbourhood charts. A results
folder that only wants *the energy-flow diagram for each planning horizon* pays
for all of that and gets one long page to scroll.

This module is the thin path. It parses the ``.vd`` once, tags the flows once
per year, and writes one self-contained page per (year, aggregation level) plus
a small index. The diagrams are the *same* diagrams: the link builders,
commodity-hub collapse, netting, export colouring and hover text all come from
:mod:`times_pypsa.qa` unchanged, so a page here and the corresponding QA chart
cannot drift apart.

Two levels are written by default — ``custom`` (the readable working level, see
``aggregation.md``) and ``Aggregation Level 2`` (the grain
``extraction_rules.csv`` filters on, i.e. the level the process mapping and the
soft-link extraction actually use). Both are rendered from one parse.
"""

from __future__ import annotations

import html
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from times_pypsa.aggregation import (
    LEVEL_ALIASES,
    available_agg_levels,
)
from times_pypsa.model import TimesAnnualFlows, load_times_annual_flows
from times_pypsa.pipeline import (
    LIBRARY_VERSION,
    PipelineConfig,
    default_mappings_dir,
    load_extraction_rules,
    load_metadata,
)
from times_pypsa.qa import (
    _process_activity_units,
    aggregate_system_flows,
    prepare_system_sankey_links,
    tag_flows_with_rules,
)
from times_pypsa.sankey_html import (
    assemble_interactive_report_html,
    build_sankey_dataset,
    collect_typed_nodes,
    links_to_records,
)
from times_pypsa.units import (
    EnergyUnit,
    default_flow_threshold,
    display_to_pj,
    unit_label,
)

logger = logging.getLogger(__name__)

# `custom` first: it is the level to look at, and the one testing/troubleshooting
# should use (README). `Aggregation Level 2` is the extraction grain — the level
# the process mapping filters on — kept alongside so a suspicious soft-link flow
# can be traced at the resolution the rules actually see.
DEFAULT_PAGE_LEVELS: tuple[str, ...] = ("custom", "Aggregation Level 2")

DEFAULT_INDEX_NAME = "times_sankey_index.html"
FILENAME_TEMPLATE = "times_sankey_{level}_{year}.html"

# Reverse LEVEL_ALIASES so `Aggregation Level 2` becomes the documented short
# alias `mapping` in file names instead of `aggregation-level-2`.
_LEVEL_SLUGS: dict[str, str] = {target: alias for alias, target in LEVEL_ALIASES.items()}

_LEVEL_BLURB: dict[str, str] = {
    "custom": (
        "Working level: export-touching <code>Aggregation Level 2</code> labels "
        "are kept, everything else collapses into readable context buckets "
        "(~45 nodes). This is the view to read the whole system on."
    ),
    "Aggregation Level 2": (
        "Extraction level: the process-mapping grain that "
        "<code>extraction_rules.csv</code> filters on, so every soft-linked "
        "flow appears exactly as the extraction sees it. Hundreds of nodes — "
        "use it to trace one flow, not to read the system."
    ),
    "sankey_overview": "Coarse overview clusters (<=20 nodes).",
    "Sector": "Sector codes only (~15 nodes).",
}


def level_slug(level: str) -> str:
    """File-name token for an aggregation level (``Aggregation Level 2`` -> ``mapping``)."""
    alias = _LEVEL_SLUGS.get(level)
    if alias:
        return alias.lower()
    slug = "".join(c if c.isalnum() else "-" for c in str(level).strip().lower())
    while "--" in slug:
        slug = slug.replace("--", "-")
    return slug.strip("-") or "level"


def sankey_page_name(level: str, year: int) -> str:
    """Name of the page written for one (level, year) pair."""
    return FILENAME_TEMPLATE.format(level=level_slug(level), year=int(year))


def sankey_page_names(
    years: Iterable[int],
    agg_levels: Iterable[str] = DEFAULT_PAGE_LEVELS,
    *,
    index_name: str | None = DEFAULT_INDEX_NAME,
) -> list[str]:
    """
    Every file :func:`export_sankey_pages` writes, without reading the ``.vd``.

    Callers that must declare the outputs before the data exists — a Snakemake
    rule, a report manifest — use this so the file list has exactly one
    definition. ``index_name=None`` omits the index.
    """
    names = [
        sankey_page_name(level, year)
        for level in agg_levels
        for year in years
    ]
    if index_name:
        names.append(index_name)
    return names


def _validate_levels(levels: list[str], model: TimesAnnualFlows) -> None:
    """Fail on a mistyped level before any page is rendered.

    ``aggregate_flows`` raises too, but only once the first year is aggregated —
    i.e. after the ``.vd`` parse and one tagging pass. A config typo should not
    cost that, and the caller gets the whole bad list at once instead of the
    first one.
    """
    if model.flows.empty:
        # Nothing was parsed, so there is nothing to validate against. Reporting
        # every level as unknown here would blame the config for an empty .vd.
        return
    known = set(available_agg_levels(model.flows)) | set(LEVEL_ALIASES) | {"L2", "Sector"}
    unknown = [lvl for lvl in levels if lvl not in known]
    if unknown:
        raise ValueError(
            f"Unknown Sankey aggregation level(s): {unknown}. "
            f"Available: {sorted(known)}"
        )


def _nav_html(
    *,
    level: str,
    year: int,
    levels: list[str],
    years: list[int],
    index_name: str | None,
) -> str:
    """Cross-links so a page is navigable on its own, without the index."""
    parts: list[str] = []
    if index_name:
        parts.append(f"<a href='{html.escape(index_name)}'>All diagrams</a>")

    year_links = " · ".join(
        f"<strong>{y}</strong>"
        if y == year
        else f"<a href='{html.escape(sankey_page_name(level, y))}'>{y}</a>"
        for y in years
    )
    parts.append(f"Year: {year_links}")

    if len(levels) > 1:
        level_links = " · ".join(
            f"<strong>{html.escape(lvl)}</strong>"
            if lvl == level
            else f"<a href='{html.escape(sankey_page_name(lvl, year))}'>{html.escape(lvl)}</a>"
            for lvl in levels
        )
        parts.append(f"Aggregation: {level_links}")

    return f"<p class='meta'>{' &nbsp;|&nbsp; '.join(parts)}</p>"


def _page_html(
    *,
    chart: dict,
    level: str,
    year: int,
    levels: list[str],
    years: list[int],
    index_name: str | None,
    title: str,
    scenario_label: str,
    units: EnergyUnit,
    vd_name: str,
    flow_threshold_display: float,
    empty: bool = False,
) -> str:
    scenario_bit = f" — {html.escape(scenario_label)}" if scenario_label else ""
    empty_banner = (
        "<p class='alarm'><strong>No energy flows for "
        f"{year}</strong> in <code>{html.escape(vd_name)}</code>. The page is "
        "written because it was requested; the diagram below is empty. Check the "
        "horizon against the years the .vd actually contains.</p>"
        if empty
        else ""
    )
    header = (
        f"<h1>{html.escape(title)}{scenario_bit}</h1>\n"
        + _nav_html(
            level=level,
            year=year,
            levels=levels,
            years=years,
            index_name=index_name,
        )
        + f"\n<p class='meta'>{_LEVEL_BLURB.get(level, '')}</p>"
        + empty_banner
    )
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    footer = (
        "<hr><p class='meta'>"
        f"TIMES source: <code>{html.escape(vd_name)}</code> · "
        f"aggregation level <code>{html.escape(level)}</code> · "
        f"unit {unit_label(units)} · "
        f"link threshold {flow_threshold_display:g} {unit_label(units)} · "
        f"times_pypsa {LIBRARY_VERSION} · generated {stamp}"
        "</p>"
    )
    return assemble_interactive_report_html(
        page_title=f"{title} {year} ({level})",
        header_html=header,
        charts=[chart],
        footer_html=footer,
    )


def _index_html(
    *,
    title: str,
    scenario_label: str,
    levels: list[str],
    years: list[int],
    empty_pages: set[tuple[str, int]],
    units: EnergyUnit,
    vd_name: str,
) -> str:
    scenario_bit = f" — {html.escape(scenario_label)}" if scenario_label else ""
    head_cells = "".join(f"<th>{html.escape(lvl)}</th>" for lvl in levels)
    rows: list[str] = []
    for year in years:
        cells: list[str] = []
        for level in levels:
            name = sankey_page_name(level, year)
            if (level, year) in empty_pages:
                cells.append(
                    f"<td><a href='{html.escape(name)}'>{year}</a> "
                    "<span class='bad'>(no flows)</span></td>"
                )
            else:
                cells.append(f"<td><a href='{html.escape(name)}'>{year}</a></td>")
        rows.append(f"<tr><th>{year}</th>{''.join(cells)}</tr>")

    blurbs = "".join(
        f"<li><strong>{html.escape(lvl)}</strong> — {_LEVEL_BLURB.get(lvl, '')}</li>"
        for lvl in levels
    )
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(title)} — index</title>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 24px; max-width: 900px; }}
    h1 {{ color: #222; }}
    .meta {{ color: #555; }}
    .bad {{ color: #b00; }}
    table {{ border-collapse: collapse; margin: 16px 0; }}
    th, td {{ border: 1px solid #ccc; padding: 6px 12px; text-align: left; }}
    th {{ background: #f0f0f0; }}
    ul {{ color: #333; line-height: 1.5; }}
  </style>
</head>
<body>
<h1>{html.escape(title)}{scenario_bit}</h1>
<p class="meta">
  Interactive TIMES energy-flow diagrams, one page per planning horizon and
  aggregation level. Each page has a netting toggle; hover a ribbon for its
  commodity, value and soft-link export status.
</p>
<table>
  <tr><th>Year</th>{head_cells}</tr>
  {''.join(rows)}
</table>
<ul>{blurbs}</ul>
<p class="meta">
  TIMES source: <code>{html.escape(vd_name)}</code> · unit {unit_label(units)} ·
  times_pypsa {LIBRARY_VERSION} · generated {stamp}
</p>
</body>
</html>
"""


def export_sankey_pages(
    out_dir: Path | str,
    *,
    vd_file: Path | str | None = None,
    mappings_dir: Path | str | None = None,
    years: Iterable[int] | None = None,
    agg_levels: Iterable[str] = DEFAULT_PAGE_LEVELS,
    units: EnergyUnit = "twh",
    flow_threshold: float | None = None,
    title: str = "TIMES energy flows",
    scenario_label: str = "",
    write_index: bool = True,
    index_name: str | None = DEFAULT_INDEX_NAME,
    config: PipelineConfig | None = None,
    model: TimesAnnualFlows | None = None,
    vdt_file: Path | str | None = None,
) -> dict[str, Path]:
    """
    Write one interactive Sankey page per (year, aggregation level) into ``out_dir``.

    ``years`` defaults to every year in the model. A requested year with no
    energy flows still gets a page — one that says so — because the caller
    (a Snakemake rule, a report index) declared that file and a silent gap
    would fail the run instead of showing the gap. Missing years are logged as
    warnings and flagged on the index.

    ``flow_threshold`` is in ``units`` (default: keep every ribbon). Pass
    ``model`` to reuse a model already loaded; otherwise ``vd_file`` is parsed
    once and shared by every page.

    Returns ``{artifact_key: path}`` with keys ``"<level_slug>_<year>"`` and,
    when written, ``"index"``.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    requested_years = [int(y) for y in years] if years is not None else None
    levels = [str(lvl) for lvl in agg_levels]
    if not levels:
        raise ValueError("agg_levels is empty; nothing to render")

    if model is None:
        if vd_file is None:
            raise ValueError("Pass either `vd_file` or an already-loaded `model`")
        if config is None:
            # Read the .vd from the earliest requested horizon. The
            # PipelineConfig default (2021) silently drops anything before it,
            # so a run whose first horizon is 2020 would get an empty page
            # rather than its diagram.
            config = PipelineConfig(
                start_year=min(requested_years) if requested_years else PipelineConfig().start_year
            )
        mappings_dir = Path(mappings_dir or default_mappings_dir())
        model = load_times_annual_flows(
            vd_file, mappings_dir, vdt_file=vdt_file, config=config
        )
    else:
        mappings_dir = Path(mappings_dir or model.mappings_dir)

    vd_name = Path(vd_file).name if vd_file is not None else "(preloaded model)"

    metadata = load_metadata(mappings_dir)
    _validate_levels(levels, model)
    rules = load_extraction_rules(metadata.extraction_rules_file)
    process_units = _process_activity_units(metadata.processes_df)

    model_years = set(model.years)
    page_years = requested_years if requested_years is not None else sorted(model_years)
    if not page_years:
        logger.warning("No years to render from %s; no Sankey pages written.", vd_name)
        return {}

    missing = [y for y in page_years if y not in model_years]
    if missing:
        logger.warning(
            "Years %s are not in %s (model has %s); their pages will say so.",
            missing,
            vd_name,
            sorted(model_years),
        )

    if flow_threshold is None:
        flow_threshold = default_flow_threshold(units)
    flow_threshold_pj = display_to_pj(flow_threshold, units)

    artifacts: dict[str, Path] = {}
    empty_pages: set[tuple[str, int]] = set()

    for year in page_years:
        # One tag pass per year, shared by every aggregation level: tagging is
        # the expensive step (it runs every extraction rule over the year), and
        # it does not depend on the level.
        flows = model.energy_flows(year) if year in model_years else pd.DataFrame()
        tagged = (
            tag_flows_with_rules(flows, rules, apply_netting=False)
            if not flows.empty
            else flows
        )

        for level in levels:
            netted: dict[int, list] = {year: []}
            gross: dict[int, list] = {year: []}
            nodes: dict[int, list] = {year: []}

            if not tagged.empty:
                year_links: list[pd.DataFrame] = []
                for apply_netting, store in ((True, netted), (False, gross)):
                    system_agg = aggregate_system_flows(
                        tagged, agg_level=level, apply_netting=apply_netting
                    )
                    links = prepare_system_sankey_links(
                        tagged,
                        flow_threshold=flow_threshold_pj,
                        apply_netting=apply_netting,
                        agg_level=level,
                        process_activity_units=process_units,
                        system_agg=system_agg,
                    )
                    store[year] = links_to_records(links, units=units)
                    year_links.append(links)
                nodes[year] = collect_typed_nodes(
                    pd.concat(year_links, ignore_index=True)
                    if year_links
                    else pd.DataFrame()
                )

            if not netted[year] and not gross[year]:
                empty_pages.add((level, year))
                logger.warning(
                    "No Sankey links for %d at level %r — writing an empty page.",
                    year,
                    level,
                )

            chart = build_sankey_dataset(
                chart_id=f"system-{level_slug(level)}-{year}",
                title=f"{title} — {level}",
                years=[year],
                netted_by_year=netted,
                gross_by_year=gross,
                nodes_by_year=nodes,
                units=units,
                subtitle=(
                    "All energy-carrier flows. Commodity hubs are collapsed to "
                    "<strong>process→process</strong> links (commodity on hover); "
                    "magenta <strong>U ·</strong> nodes are final-demand / non-PJ "
                    "sinks. Links exported to pypsa-wal are coloured by PyPSA "
                    "sector — Industry (blue), Transport (orange), Residential "
                    "(red), Services (teal), Agriculture (brown); grey = not "
                    "exported."
                ),
            )

            page = out_dir / sankey_page_name(level, year)
            page.write_text(
                _page_html(
                    chart=chart,
                    level=level,
                    year=year,
                    levels=levels,
                    years=page_years,
                    index_name=index_name if write_index else None,
                    title=title,
                    scenario_label=scenario_label,
                    units=units,
                    vd_name=vd_name,
                    flow_threshold_display=flow_threshold,
                    empty=(level, year) in empty_pages,
                ),
                encoding="utf-8",
            )
            artifacts[f"{level_slug(level)}_{year}"] = page
            logger.info("Wrote %s", page)

    if write_index and index_name:
        index_path = out_dir / index_name
        index_path.write_text(
            _index_html(
                title=title,
                scenario_label=scenario_label,
                levels=levels,
                years=page_years,
                empty_pages=empty_pages,
                units=units,
                vd_name=vd_name,
            ),
            encoding="utf-8",
        )
        artifacts["index"] = index_path
        logger.info("Wrote %s", index_path)

    return artifacts
