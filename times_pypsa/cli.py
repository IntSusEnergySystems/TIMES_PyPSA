"""Command-line interface for times_pypsa."""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

from times_pypsa.pipeline import (
    PipelineConfig,
    default_mappings_dir,
    export_all_horizons,
    export_coupling_dir,
    export_horizon,
    generate_sankey,
)
from times_pypsa.indicator_pages import export_indicator_pages
from times_pypsa.qa import generate_qa_report
from times_pypsa.sankey_pages import DEFAULT_PAGE_LEVELS, export_sankey_pages
from times_pypsa.units import DEFAULT_FLOW_THRESHOLD_TWH

logger = logging.getLogger(__name__)


def _parse_horizons(value: str) -> list[int]:
    if "-" in value and "," not in value:
        start, end = value.split("-", 1)
        return list(range(int(start), int(end) + 1))
    return [int(v.strip()) for v in value.split(",") if v.strip()]


def _configure_logging(verbose: bool) -> None:
    level = logging.DEBUG if verbose else logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s: %(message)s",
    )


def _add_common_args(parser: argparse.ArgumentParser) -> None:
    parser.add_argument(
        "--vd",
        required=True,
        type=Path,
        help="Path to TIMES .vd output file",
    )
    parser.add_argument(
        "--mappings-dir",
        type=Path,
        default=None,
        help="Directory with mapping CSVs (default: repository data/)",
    )
    parser.add_argument(
        "--start-year",
        type=int,
        default=2021,
        help="Earliest year to read from the .vd file (default: 2021)",
    )
    parser.add_argument(
        "-v",
        "--verbose",
        action="store_true",
        help="Enable debug logging",
    )


def _build_config(args: argparse.Namespace) -> PipelineConfig:
    return PipelineConfig(
        start_year=args.start_year,
        agg_level=getattr(args, "agg_level", "Aggregation Level 2"),
    )


def _resolve_mappings_dir(args: argparse.Namespace) -> Path:
    return args.mappings_dir or default_mappings_dir()


def cmd_export_coupling(args: argparse.Namespace) -> int:
    mappings_dir = _resolve_mappings_dir(args)
    config = _build_config(args)
    horizons = _parse_horizons(args.horizons)
    export_coupling_dir(
        args.coupling_dir,
        args.vd,
        horizons,
        mappings_dir=mappings_dir,
        config=config,
    )
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    mappings_dir = _resolve_mappings_dir(args)
    config = _build_config(args)
    horizons = _parse_horizons(args.horizons)
    out_dir = args.out
    out_dir.mkdir(parents=True, exist_ok=True)

    emit = args.emit
    if emit == "demands":
        export_all_horizons(
            args.vd, mappings_dir, horizons, out_dir, emit="demands", config=config
        )
    elif emit == "sankey":
        export_all_horizons(
            args.vd, mappings_dir, horizons, out_dir, emit="sankey", config=config
        )
    else:
        export_all_horizons(
            args.vd, mappings_dir, horizons, out_dir, emit="all", config=config
        )
    return 0


def cmd_sankey(args: argparse.Namespace) -> int:
    mappings_dir = _resolve_mappings_dir(args)
    config = _build_config(args)
    generate_sankey(args.vd, mappings_dir, args.year, args.out_dir, config=config)
    return 0


def cmd_sankey_pages(args: argparse.Namespace) -> int:
    mappings_dir = _resolve_mappings_dir(args)
    years = _parse_horizons(args.years) if args.years else None
    # `--start-year` defaults to None here (not 2021) so that, unset,
    # export_sankey_pages can start the .vd read at the earliest requested year
    # instead of silently dropping a horizon before the library default.
    config = (
        PipelineConfig(start_year=args.start_year)
        if args.start_year is not None
        else None
    )
    artifacts = export_sankey_pages(
        args.out_dir,
        vd_file=args.vd,
        mappings_dir=mappings_dir,
        years=years,
        agg_levels=args.agg_levels or list(DEFAULT_PAGE_LEVELS),
        units=args.units,
        flow_threshold=args.threshold,
        scenario_label=args.scenario_label,
        config=config,
    )
    if not artifacts:
        logger.error("No Sankey pages written (empty .vd?)")
        return 1
    logger.info("Wrote %d file(s) to %s", len(artifacts), args.out_dir)
    return 0


def cmd_indicators(args: argparse.Namespace) -> int:
    mappings_dir = _resolve_mappings_dir(args)
    years = _parse_horizons(args.years) if args.years else None
    # Like `sankey-pages`: unset `--start-year` means "start at the earliest
    # requested horizon", not the library default of 2021, so asking for 2020
    # does not silently produce an empty first bar.
    config = (
        PipelineConfig(start_year=args.start_year)
        if args.start_year is not None
        else None
    )
    artifacts = export_indicator_pages(
        args.out_dir,
        vd_file=args.vd,
        mappings_dir=mappings_dir,
        years=years,
        scenario_label=args.scenario_label,
        write_csv=not args.no_csv,
        config=config,
    )
    if not artifacts:
        logger.error("No indicator pages written (empty .vd?)")
        return 1
    logger.info("Wrote %d file(s) to %s", len(artifacts), args.out_dir)
    return 0


def cmd_qa(args: argparse.Namespace) -> int:
    mappings_dir = _resolve_mappings_dir(args)
    config = _build_config(args)
    if args.year:
        parsed = _parse_horizons(args.year)
        year = parsed[0] if len(parsed) == 1 else parsed
    else:
        year = None
    artifacts = generate_qa_report(
        args.vd,
        args.out_dir,
        year,
        vdt_file=args.vdt,
        mappings_dir=mappings_dir,
        config=config,
        flow_threshold_l0=args.threshold_l0,
        flow_threshold_l1=args.threshold_l1,
        flow_threshold_export=args.threshold_export,
        units=args.units,
        agg_level=args.agg_level,
    )
    if not artifacts:
        logger.error("QA report produced no artifacts (empty flows?)")
        return 1
    logger.info("QA report: %s", artifacts.get("report"))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="times-pypsa",
        description="Extract PyPSA demands and Sankey diagrams from TIMES .vd files",
    )
    subparsers = parser.add_subparsers(dest="command", required=True)

    export_parser = subparsers.add_parser(
        "export", help="Export demands and/or Sankey for multiple horizons"
    )
    _add_common_args(export_parser)
    export_parser.add_argument(
        "--out",
        type=Path,
        required=True,
        help="Output directory",
    )
    export_parser.add_argument(
        "--horizons",
        default="2021-2050",
        help="Horizons as comma list (2030,2040) or range (2021-2050)",
    )
    export_parser.add_argument(
        "--emit",
        choices=["sankey", "demands", "all"],
        default="all",
        help="What to emit (default: all)",
    )
    export_parser.set_defaults(func=cmd_export)

    coupling_parser = subparsers.add_parser(
        "export-coupling",
        help="Export a PyPSA-WAL soft-linking bundle (demands + manifest)",
    )
    _add_common_args(coupling_parser)
    coupling_parser.add_argument(
        "--coupling-dir",
        type=Path,
        required=True,
        help="Root directory for the coupling bundle",
    )
    coupling_parser.add_argument(
        "--horizons",
        default="2025,2030,2040,2050",
        help="Horizons as comma list (2030,2040) or range (2021-2050)",
    )
    coupling_parser.set_defaults(func=cmd_export_coupling)

    sankey_parser = subparsers.add_parser(
        "sankey", help="Generate Sankey diagram for a single year"
    )
    _add_common_args(sankey_parser)
    sankey_parser.add_argument(
        "--year",
        type=int,
        required=True,
        help="Planning horizon / model year",
    )
    sankey_parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Output directory for HTML and auxiliary CSVs",
    )
    sankey_parser.add_argument(
        "--agg-level",
        default="Aggregation Level 2",
        help=(
            "Sankey aggregation column shared by mapping_processes.csv and "
            "mapping_commodities.csv: Sector, Aggregation Level 2 (alias "
            "mapping), custom, sankey_overview (aliases: L0, L2)"
        ),
    )
    sankey_parser.set_defaults(func=cmd_sankey)

    pages_parser = subparsers.add_parser(
        "sankey-pages",
        help=(
            "One standalone Sankey page per model year x aggregation level "
            "(for a report / results folder), plus an index"
        ),
    )
    _add_common_args(pages_parser)
    pages_parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Output directory for the HTML pages (e.g. a results html/ folder)",
    )
    pages_parser.add_argument(
        "--years",
        default=None,
        help=(
            "Years as comma list (2030,2040) or range (2025-2050). "
            "Default: every year in the .vd file."
        ),
    )
    pages_parser.add_argument(
        "--agg-levels",
        nargs="+",
        default=None,
        help=(
            "Aggregation levels to write, one page each per year "
            f"(default: {' '.join(DEFAULT_PAGE_LEVELS)!r})"
        ),
    )
    pages_parser.add_argument(
        "--units",
        choices=["twh", "pj"],
        default="twh",
        help="Energy unit for the diagrams (default: twh)",
    )
    pages_parser.add_argument(
        "--threshold",
        type=float,
        default=None,
        help=(
            "Minimum link size in selected --units "
            f"(default: {DEFAULT_FLOW_THRESHOLD_TWH:g}; set >0 to hide small ribbons)"
        ),
    )
    pages_parser.add_argument(
        "--scenario-label",
        default="",
        help="Scenario name shown in the page heading",
    )
    pages_parser.set_defaults(func=cmd_sankey_pages, start_year=None)

    indicators_parser = subparsers.add_parser(
        "indicators",
        help=(
            "Scenario indicator pages (final energy demand, emissions, heat "
            "production, power fleet) with the tables they are built from"
        ),
    )
    _add_common_args(indicators_parser)
    indicators_parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Output directory for the HTML pages and CSV tables",
    )
    indicators_parser.add_argument(
        "--years",
        default=None,
        help=(
            "Years as comma list (2030,2040) or range (2025-2050). "
            "Default: every year in the .vd file."
        ),
    )
    indicators_parser.add_argument(
        "--scenario-label",
        default="",
        help="Scenario name shown in the page heading",
    )
    indicators_parser.add_argument(
        "--no-csv",
        action="store_true",
        help="Write only the HTML pages, not the CSV tables beside them",
    )
    indicators_parser.set_defaults(func=cmd_indicators, start_year=None)

    qa_parser = subparsers.add_parser(
        "qa",
        help="Multi-view Sankey QA report with export highlighting and balance tables",
    )
    _add_common_args(qa_parser)
    qa_parser.add_argument(
        "--year",
        default=None,
        help=(
            "Planning horizon(s): single year (2050), comma list (2030,2040,2050), "
            "or range (2025-2050). Default: all years in the .vd file."
        ),
    )
    qa_parser.add_argument(
        "--out-dir",
        type=Path,
        required=True,
        help="Output directory for HTML report and CSV diagnostics",
    )
    qa_parser.add_argument(
        "--vdt",
        type=Path,
        default=None,
        help="Optional TIMES .vdt topology file for mismatch checks",
    )
    qa_parser.add_argument(
        "--units",
        choices=["twh", "pj"],
        default="twh",
        help="Energy unit for Sankeys, CSVs, and HTML tables (default: twh)",
    )
    qa_parser.add_argument(
        "--threshold-export",
        type=float,
        default=None,
        help=(
            "Minimum Sankey link size in selected --units "
            f"(default: {DEFAULT_FLOW_THRESHOLD_TWH:g}; set >0 to hide small ribbons)"
        ),
    )
    qa_parser.add_argument(
        "--threshold-l0",
        type=float,
        default=0.5,
        help="Deprecated (kept for CLI compat); unused by export neighbourhood view",
    )
    qa_parser.add_argument(
        "--threshold-l1",
        type=float,
        default=0.5,
        help="Deprecated (kept for CLI compat); unused by export neighbourhood view",
    )
    qa_parser.add_argument(
        "--agg-level",
        default="Aggregation Level 2",
        help=(
            "Sankey aggregation column shared by mapping_processes.csv and "
            "mapping_commodities.csv: Sector, Aggregation Level 2 (alias "
            "mapping), custom, sankey_overview (aliases: L0, L2)"
        ),
    )
    qa_parser.set_defaults(func=cmd_qa)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
