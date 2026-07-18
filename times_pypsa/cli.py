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
from times_pypsa.qa import generate_qa_report
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
        help="Directory with mapping CSVs (default: bundled package mappings)",
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
    return PipelineConfig(start_year=args.start_year)


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
    sankey_parser.set_defaults(func=cmd_sankey)

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
            f"(default: {DEFAULT_FLOW_THRESHOLD_TWH:g} TWh ≈ 1 PJ when --units twh)"
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
    qa_parser.set_defaults(func=cmd_qa)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    _configure_logging(args.verbose)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
