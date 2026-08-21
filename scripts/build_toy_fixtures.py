#!/usr/bin/env python3
"""Build small .vd / .vdt fixtures for fast integration tests.

Reads the full scenario under data/, keeps only energy-flow variables
(VAR_FIn / VAR_FOut / VAR_Comnet) plus installed capacity (VAR_Cap),
soft-link years, and pre-aggregates timeslices to ANNUAL so the file is ~40×
smaller while preserving annual totals used by QA / extraction tests.

``VAR_Cap`` is kept so the heating-capacity export
(:mod:`times_pypsa.heat_softlink`) is testable without the full scenario, and
``VAR_Act`` so the road-transport export
(:mod:`times_pypsa.transport_softlink`) is too — the fixtures committed before
2026-08-21 predate it, so the activity assertions skip on those.
``VAR_Ncap`` is deliberately **not** kept: it is already contained in
``VAR_Cap``, and a fixture that carries it invites the double count the
capacity export used to make.

Also writes a leaner ``toy_qa.*`` pair (two years, top export categories
only) for the expensive ``generate_qa_report`` tests.
"""

from __future__ import annotations

import argparse
from collections import defaultdict
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
DEFAULT_VD = REPO_ROOT / "data" / "scen_corrige_251129_0112.vd"
DEFAULT_VDT = REPO_ROOT / "data" / "scen_corrige_251129_0112.vdt"
DEFAULT_OUT = REPO_ROOT / "tests" / "fixtures"
DEFAULT_MAPPINGS = REPO_ROOT / "data"
KEEP_VARS = {"VAR_FIN", "VAR_FOUT", "VAR_COMNET", "VAR_CAP", "VAR_ACT"}
DEFAULT_YEARS = (2025, 2030, 2040, 2050)
QA_YEARS = (2030, 2050)
QA_TOP_CATEGORIES = 3


def parse_quoted_csv(line: str) -> list[str]:
    parts: list[str] = []
    current = ""
    in_quotes = False
    for char in line.strip():
        if char == '"':
            in_quotes = not in_quotes
        elif char == "," and not in_quotes:
            parts.append(current.strip('"'))
            current = ""
        else:
            current += char
    parts.append(current.strip('"'))
    return parts


def _header_for(scenario_id: str, header_lines: list[str]) -> list[str]:
    out: list[str] = []
    for h in header_lines:
        if "ImportID-" in h:
            out.append(f"*         ImportID- Scenario:{scenario_id}\n")
        else:
            out.append(h)
    return out


def _write_vd(
    dest: Path,
    header_lines: list[str],
    agg: dict[tuple[str, str, str, str, str, str], float],
    scenario_id: str,
) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with dest.open("w", encoding="utf-8", newline="\n") as out:
        out.writelines(_header_for(scenario_id, header_lines))
        for (variable, commodity, process, year, region, vintage), value in sorted(
            agg.items(), key=lambda kv: (kv[0][3], kv[0][0], kv[0][2], kv[0][1])
        ):
            out.write(
                f'"{variable}","{commodity}","{process}","{year}",'
                f'"{region}","{vintage}","ANNUAL","-",{value}\n'
            )


def aggregate_energy_rows(
    src: Path,
    years: set[int],
) -> tuple[list[str], dict[tuple[str, str, str, str, str, str], float], set[str], set[str]]:
    """Stream .vd → annual FIN/FOUT/Comnet rows for selected years."""
    header_lines: list[str] = []
    agg: dict[tuple[str, str, str, str, str, str], float] = defaultdict(float)
    processes: set[str] = set()
    commodities: set[str] = set()
    saw_data = False

    with src.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            if not line.strip() or line.startswith("*"):
                if not saw_data:
                    header_lines.append(line if line.endswith("\n") else line + "\n")
                continue
            parts = parse_quoted_csv(line)
            if len(parts) < 9:
                continue
            if parts[0].upper() not in KEEP_VARS:
                continue
            try:
                year = int(parts[3])
            except ValueError:
                continue
            if year not in years:
                continue
            try:
                value = float(parts[8])
            except ValueError:
                continue
            if value == 0:
                continue
            saw_data = True
            key = (parts[0], parts[1], parts[2], parts[3], parts[4], parts[5])
            agg[key] += value
            if parts[2] and parts[2] != "-":
                processes.add(parts[2])
            if parts[1] and parts[1] != "-":
                commodities.add(parts[1])

    return header_lines, agg, processes, commodities


def build_toy_vdt(
    src: Path,
    dest: Path,
    processes: set[str],
    *,
    scenario_id: str = "toy_scen_corrige",
) -> int:
    """Keep topology links for processes present in the toy .vd."""
    n_out = 0
    dest.parent.mkdir(parents=True, exist_ok=True)
    with src.open(encoding="utf-8", errors="replace") as f, dest.open(
        "w", encoding="utf-8", newline="\n"
    ) as out:
        out.write("*VFEPATH=\n")
        out.write(f"ScenDesc={scenario_id}\n")
        out.write(f"ScenEDesc={scenario_id}\n")
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("*") or "=" in stripped[:20]:
                continue
            if not stripped.startswith('"'):
                continue
            parts = parse_quoted_csv(stripped)
            if len(parts) < 4:
                continue
            process, commodity, direction = parts[1], parts[2], parts[3].upper()
            if direction not in {"IN", "OUT"}:
                continue
            if process not in processes:
                continue
            out.write(f'"{parts[0]}","{process}","{commodity}","{direction}"\n')
            n_out += 1
    return n_out


def _qa_process_set(
    toy_vd: Path,
    toy_vdt: Path | None,
    mappings_dir: Path,
    *,
    n_categories: int,
    years: set[int],
) -> set[str]:
    """Processes belonging to the top-N export categories plus one-hop neighbours."""
    from times_pypsa.model import load_times_annual_flows
    from times_pypsa.pipeline import PipelineConfig, load_extraction_rules, load_metadata
    from times_pypsa.qa import tag_flows_with_rules, category_pj_totals

    model = load_times_annual_flows(
        toy_vd,
        mappings_dir,
        vdt_file=toy_vdt,
        config=PipelineConfig(start_year=min(years)),
    )
    rules = load_extraction_rules(load_metadata(mappings_dir).extraction_rules_file)
    year = 2030 if 2030 in model.years else model.years[0]
    flows = model.energy_flows(year)
    tagged = tag_flows_with_rules(flows, rules, apply_netting=False)
    totals = category_pj_totals(flows, rules, apply_netting=True)
    top = [c for c, pj in sorted(totals.items(), key=lambda x: -x[1]) if pj > 0][
        :n_categories
    ]

    keep: set[str] = set()
    for cat in top:
        mask = tagged["matched_categories"].map(
            lambda xs, c=cat: isinstance(xs, list) and c in xs
        )
        keep.update(tagged.loc[mask, "process_code"].astype(str))

    # One-hop neighbourhood on the same year (shared commodities / processes)
    core = tagged[tagged["process_code"].astype(str).isin(keep)]
    core_com = set(core["commodity_code"].astype(str))
    touch = tagged[
        tagged["process_code"].astype(str).isin(keep)
        | tagged["commodity_code"].astype(str).isin(core_com)
    ]
    keep.update(touch["process_code"].astype(str))
    keep.discard("-")
    keep.discard("nan")
    return keep


def filter_agg_by_processes(
    agg: dict[tuple[str, str, str, str, str, str], float],
    processes: set[str],
    years: set[int],
) -> dict[tuple[str, str, str, str, str, str], float]:
    """Keep FIN/FOUT for selected processes; keep Comnet for their commodities."""
    commodities: set[str] = set()
    for (_var, com, proc, year, _reg, _vin), _val in agg.items():
        if int(year) in years and proc in processes:
            if com and com != "-":
                commodities.add(com)

    out: dict[tuple[str, str, str, str, str, str], float] = {}
    for key, val in agg.items():
        variable, commodity, process, year, _region, _vintage = key
        if int(year) not in years:
            continue
        vu = variable.upper()
        if vu == "VAR_COMNET":
            if commodity in commodities:
                out[key] = val
        elif process in processes:
            out[key] = val
    return out


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--vd", type=Path, default=DEFAULT_VD)
    parser.add_argument("--vdt", type=Path, default=DEFAULT_VDT)
    parser.add_argument("--out-dir", type=Path, default=DEFAULT_OUT)
    parser.add_argument(
        "--years",
        type=int,
        nargs="+",
        default=list(DEFAULT_YEARS),
        help="Years to keep in toy_scen (default: soft-link horizons)",
    )
    parser.add_argument(
        "--qa-categories",
        type=int,
        default=QA_TOP_CATEGORIES,
        help="Top export categories to keep in toy_qa",
    )
    args = parser.parse_args()
    if not args.vd.exists():
        raise SystemExit(f"Source .vd not found: {args.vd}")

    years = set(args.years)
    header, agg, processes, commodities = aggregate_energy_rows(args.vd, years)

    vd_out = args.out_dir / "toy_scen.vd"
    vdt_out = args.out_dir / "toy_scen.vdt"
    _write_vd(vd_out, header, agg, "toy_scen_corrige")
    print(
        f"Wrote {vd_out} ({vd_out.stat().st_size / 1e6:.2f} MB): "
        f"{len(agg)} annual rows, {len(processes)} processes, "
        f"{len(commodities)} commodities"
    )

    if args.vdt.exists():
        n_links = build_toy_vdt(args.vdt, vdt_out, processes)
        print(
            f"Wrote {vdt_out} ({vdt_out.stat().st_size / 1e6:.2f} MB): "
            f"{n_links} topology links"
        )
    else:
        print(f"No .vdt at {args.vdt}; skipped topology fixture")
        vdt_out = None

    # Lean QA fixture: two years, top export-category neighbourhood only
    qa_years = set(QA_YEARS) & years or years
    qa_procs = _qa_process_set(
        vd_out,
        vdt_out if vdt_out and vdt_out.exists() else None,
        DEFAULT_MAPPINGS,
        n_categories=args.qa_categories,
        years=qa_years,
    )
    qa_agg = filter_agg_by_processes(agg, qa_procs, qa_years)
    qa_vd = args.out_dir / "toy_qa.vd"
    qa_vdt = args.out_dir / "toy_qa.vdt"
    _write_vd(qa_vd, header, qa_agg, "toy_qa_corrige")
    qa_proc_names = {
        proc for (_v, _c, proc, _y, _r, _vi) in qa_agg if proc and proc != "-"
    }
    print(
        f"Wrote {qa_vd} ({qa_vd.stat().st_size / 1e6:.2f} MB): "
        f"{len(qa_agg)} annual rows, {len(qa_proc_names)} processes "
        f"(top {args.qa_categories} categories, years {sorted(qa_years)})"
    )
    if args.vdt.exists():
        n_links = build_toy_vdt(
            args.vdt, qa_vdt, qa_proc_names, scenario_id="toy_qa_corrige"
        )
        print(
            f"Wrote {qa_vdt} ({qa_vdt.stat().st_size / 1e6:.2f} MB): "
            f"{n_links} topology links"
        )


if __name__ == "__main__":
    main()
