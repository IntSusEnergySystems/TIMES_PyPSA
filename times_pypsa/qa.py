"""Multi-view Sankey QA report with soft-link export highlighting."""

from __future__ import annotations

import colorsys
import logging
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from times_pypsa.aggregation import (
    PYPSA_SECTOR_COLORS,
    PYPSA_SECTOR_ORDER,
    aggregate_flows,
    assign_export_sectors,
    build_sankey_label_map,
    collapse_commodity_nodes,
    link_sector,
    net_collapsed_process_links,
    sankey_links_from_flows,
)
from times_pypsa.balances import (
    KNOWN_ZERO_CATEGORIES,
    commodity_balance_vs_comnet,
    commodity_node_residuals,
    double_count_matrix,
    empty_rule_report,
    find_loop_components,
    parent_child_sum_checks,
    process_io_ratios,
)
from times_pypsa.model import TimesAnnualFlows, load_times_annual_flows
from times_pypsa.pipeline import (
    PipelineConfig,
    _apply_extraction_rule,
    default_mappings_dir,
    load_extraction_rules,
    load_metadata,
    net_bidirectional_links,
)
from times_pypsa.units import (
    EnergyUnit,
    default_flow_threshold,
    display_to_pj,
    format_energy,
    pj_to_display,
    prepare_energy_output,
    unit_label,
)
from times_pypsa.sankey_html import (
    CONTEXT_COLOR,
    DOUBLE_COUNT_COLOR,
    EXPORTED_COLOR,
    MIXED_COLOR,
    assemble_interactive_report_html,
    build_sankey_dataset,
    collect_typed_nodes,
    export_status_color,
    export_status_hover,
    link_color,
    link_export_status,
    links_to_records,
    node_display_label,
    node_key,
)

logger = logging.getLogger(__name__)

CATEGORY_PALETTE = [
    "rgba(31, 119, 180, 0.8)",
    "rgba(255, 127, 14, 0.8)",
    "rgba(44, 160, 44, 0.8)",
    "rgba(214, 39, 40, 0.8)",
    "rgba(148, 103, 189, 0.8)",
    "rgba(140, 86, 75, 0.8)",
    "rgba(227, 119, 194, 0.8)",
    "rgba(127, 127, 127, 0.8)",
    "rgba(188, 189, 34, 0.8)",
    "rgba(23, 190, 207, 0.8)",
]


def _category_color(index: int) -> str:
    if index < len(CATEGORY_PALETTE):
        return CATEGORY_PALETTE[index]
    h = (index * 0.61803398875) % 1.0
    r, g, b = colorsys.hsv_to_rgb(h, 0.65, 0.85)
    return f"rgba({int(r*255)}, {int(g*255)}, {int(b*255)}, 0.8)"


def filter_energy_carrier_flows(df: pd.DataFrame) -> pd.DataFrame:
    """
    Keep energy-carrier flows for Sankeys (universal scope filter).

    Scope (applies equally to all soft-link categories — no process- or
    category-specific exceptions):

    - keep rows with a non-empty ``pypsa_carrier`` (mapped energy commodity)
    - drop emission / pollutant commodity codes (CO₂, GHG, SOX, …)

    Do **not** add ad-hoc drops of soft-linked processes (e.g. retrofits).
    If a TIMES structure looks odd on the Sankey, fix labelling / mapping or
    document the quirk — never hide matched export flows.
    """
    if df.empty:
        return df.copy()
    carrier = (
        df["pypsa_carrier"].astype(str).str.strip()
        if "pypsa_carrier" in df.columns
        else pd.Series("", index=df.index)
    )
    has_carrier = carrier.ne("") & carrier.str.lower().ne("nan")
    code = df["commodity_code"].astype(str)
    is_emission = code.str.contains(
        r"CO2|GHG|SOX|NOX|NH3|PM2|COV|CH4", case=False, na=False
    )
    return df.loc[has_carrier & ~is_emission].copy()


def select_export_neighborhood(
    tagged: pd.DataFrame,
    *,
    core_mask: pd.Series | None = None,
    energy_only: bool = True,
) -> pd.DataFrame:
    """
    Keep exported (or category-core) flows plus one-hop neighbours (n−1 and n+1).

    Core nodes = process_code and commodity_code appearing on matched rows.
    Neighbourhood = any energy flow that shares a core process **or** a core
    commodity. That includes:

    - n−1: producers of matched commodities; other inputs into matched processes
    - n  : the matched / exported flows themselves
    - n+1: consumers of matched commodities; other outputs from matched processes

    When ``energy_only`` is True (default), drop emission / non-carrier flows so
    the Sankey stays readable (CO₂/GHG often share processes with heat techs).
    """
    if tagged.empty:
        return tagged.copy()

    df = tagged
    if energy_only:
        df = filter_energy_carrier_flows(df)
        if df.empty:
            return df

    if core_mask is None:
        if "exported" not in df.columns:
            return df.copy()
        core_mask = df["exported"].fillna(False).astype(bool)
    else:
        core_mask = core_mask.reindex(df.index, fill_value=False)

    core = df.loc[core_mask]
    if core.empty:
        return df.iloc[0:0].copy()

    core_processes = set(core["process_code"].astype(str))
    core_commodities = set(core["commodity_code"].astype(str))

    touch = df[
        df["process_code"].astype(str).isin(core_processes)
        | df["commodity_code"].astype(str).isin(core_commodities)
    ].copy()
    return touch


def _process_activity_units(processes_df: pd.DataFrame | None) -> dict[str, str]:
    """Map process code → Activity unit from the process mapping CSV."""
    if processes_df is None or processes_df.empty:
        return {}
    code_col = (
        "Process"
        if "Process" in processes_df.columns
        else (
            "Technology (Process)"
            if "Technology (Process)" in processes_df.columns
            else None
        )
    )
    if code_col is None or "Activity unit" not in processes_df.columns:
        return {}
    out: dict[str, str] = {}
    for _, row in processes_df.iterrows():
        code = str(row[code_col]).strip()
        if not code:
            continue
        out[code] = str(row.get("Activity unit", "") or "").strip()
    return out


def aggregate_system_flows(
    tagged: pd.DataFrame,
    *,
    agg_level: str = "Aggregation Level 2",
    apply_netting: bool = True,
) -> pd.DataFrame:
    """
    Aggregate the whole energy-carrier system once for a (year, level, netting).

    This is both the system Sankey's own input and the reference view every
    neighbourhood diagram needs to tell a genuine FOut-only demand sink apart
    from a consumer its own n±1 truncation dropped. Computing it once per year
    instead of once per diagram is the bulk of the report speed-up.
    """
    df = filter_energy_carrier_flows(tagged)
    if df.empty:
        return df
    return aggregate_flows(df, level=agg_level, apply_netting=apply_netting)


def prepare_export_sankey_links(
    tagged: pd.DataFrame,
    *,
    core_mask: pd.Series | None = None,
    flow_threshold: float = 0.0,
    apply_netting: bool = True,
    agg_level: str = "Aggregation Level 2",
    collapse_commodities: bool = True,
    process_activity_units: dict[str, str] | None = None,
    reference_flows: pd.DataFrame | None = None,
    reference_agg: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Build Sankey links for the export neighbourhood using mapping CSV labels only.

    Aggregation level is controlled by ``agg_level`` (CSV column name or legacy alias).
    By default balanced commodity hubs are collapsed to process→process links.

    ``reference_agg`` is the already-aggregated full-system view. It only depends
    on ``(tagged, agg_level, apply_netting)``, so callers that build many
    neighbourhood diagrams for one year should aggregate once and pass it in —
    recomputing it per category dominated the report runtime.
    """
    nb = select_export_neighborhood(tagged, core_mask=core_mask)
    if nb.empty:
        return pd.DataFrame(
            columns=["source", "target", "value", "exported", "matched_categories"]
        )
    agg = aggregate_flows(nb, level=agg_level, apply_netting=apply_netting)
    if collapse_commodities:
        if reference_agg is None:
            # Full energy-carrier system (same agg) so FOut-only detection is not
            # fooled by neighbourhood truncation that drops legitimate consumers.
            ref_src = (
                reference_flows
                if reference_flows is not None
                else filter_energy_carrier_flows(tagged)
            )
            reference_agg = aggregate_flows(
                ref_src, level=agg_level, apply_netting=apply_netting
            )
        ref_agg = reference_agg
        links = collapse_commodity_nodes(
            agg,
            flow_threshold=flow_threshold,
            reference_flows=ref_agg,
            process_activity_units=process_activity_units,
        )
        if apply_netting:
            links = net_collapsed_process_links(links)
        return assign_export_sectors(links, agg, anchor=True)
    links = sankey_links_from_flows(agg, flow_threshold=flow_threshold)
    return assign_export_sectors(links, agg, anchor=False)


def prepare_system_sankey_links(
    tagged: pd.DataFrame,
    *,
    flow_threshold: float = 0.0,
    apply_netting: bool = True,
    agg_level: str = "Aggregation Level 2",
    collapse_commodities: bool = True,
    process_activity_units: dict[str, str] | None = None,
    reference_flows: pd.DataFrame | None = None,
    system_agg: pd.DataFrame | None = None,
) -> pd.DataFrame:
    """
    Build Sankey links for all TIMES energy carrier flows (no n−1/n+1 filter).

    Aggregation level is controlled by ``agg_level`` (CSV column name or legacy alias).
    By default balanced commodity hubs are collapsed to process→process links
    (commodities become flow labels; residual imbalance keeps a demand/imbalance sink).

    Pass ``system_agg`` (see :func:`aggregate_system_flows`) to reuse an
    aggregation already computed for the same year / level / netting; it is also
    the ``reference_agg`` the per-category diagrams need.
    """
    df = filter_energy_carrier_flows(tagged)
    if df.empty:
        return pd.DataFrame(
            columns=["source", "target", "value", "exported", "matched_categories"]
        )
    # Unfiltered energy view (same commodity labels after aggregation) for attributing
    # residuals to non-PJ consumers when those rows were dropped before collapse.
    ref = reference_flows if reference_flows is not None else df
    agg = (
        system_agg
        if system_agg is not None
        else aggregate_flows(df, level=agg_level, apply_netting=apply_netting)
    )
    if collapse_commodities:
        ref_agg = (
            aggregate_flows(ref, level=agg_level, apply_netting=apply_netting)
            if ref is not df
            else agg
        )
        links = collapse_commodity_nodes(
            agg,
            flow_threshold=flow_threshold,
            reference_flows=ref_agg,
            process_activity_units=process_activity_units,
        )
        if apply_netting:
            links = net_collapsed_process_links(links)
        return assign_export_sectors(links, agg, anchor=True)
    links = sankey_links_from_flows(agg, flow_threshold=flow_threshold)
    return assign_export_sectors(links, agg, anchor=False)


class CategoryKeyIndex(dict):
    """
    Category → matched flow keys, parked on ``DataFrame.attrs``.

    pandas deep-copies ``attrs`` inside every ``__finalize__``, i.e. on every
    slice / groupby / copy of the tagged frame. This index holds ~10⁵ tuples, so
    a plain dict here cost more than the rest of the report put together
    (deepcopy was 143 s of a 179 s two-year run). It is written once and only
    ever read, so sharing the same object across copies is safe.
    """

    def __deepcopy__(self, memo):
        return self

    def __copy__(self):
        return self


def tag_flows_with_rules(
    year_df: pd.DataFrame,
    extraction_rules: dict,
    *,
    apply_netting: bool = False,
) -> pd.DataFrame:
    """
    Tag each flow row with matched extraction categories.

    Uses the same filters as demand extraction. Netting is off by default so
    individual flow keys stay identifiable for coverage / double-count checks.

    The per-category matched flow keys land on ``df.attrs["category_keys"]`` as
    a :class:`CategoryKeyIndex`.
    """
    if year_df.empty:
        return year_df

    df = year_df.copy()
    # Rules historically filter on agg_level_1 holding Aggregation Level 2 labels.
    if "process_agg" in df.columns:
        df["agg_level_1"] = df["process_agg"]
    elif "agg_level_1_for_rules" in df.columns:
        df["agg_level_1"] = df["agg_level_1_for_rules"]

    # Precompute the flow key of every row once, vectorized: the per-category
    # loop then only has to slice it (was iterrows() per category — 60× the work).
    row_keys = pd.Series(
        list(
            zip(
                df["year"].astype(int),
                df["process_code"].astype(str),
                df["commodity_code"].astype(str),
                df["variable"].astype(str).str.upper(),
            )
        ),
        index=df.index,
    )

    matches: dict[object, list[str]] = {}
    category_keys = CategoryKeyIndex()

    for category, (var_type, filter_type, filter_values) in extraction_rules.items():
        filtered = _apply_extraction_rule(
            df, var_type, filter_type, filter_values, apply_netting=apply_netting
        )
        if filtered.empty:
            category_keys[category] = set()
            continue
        # Without netting, filtered index aligns with df
        idx = filtered.index.intersection(df.index)
        for i in idx:
            matches.setdefault(i, []).append(category)
        category_keys[category] = set(row_keys.loc[idx])

    df["matched_categories"] = [matches.get(i, []) for i in df.index]
    df["exported"] = [bool(matches.get(i)) for i in df.index]
    df.attrs["category_keys"] = category_keys
    return df


def category_pj_totals(
    year_df: pd.DataFrame,
    extraction_rules: dict,
    *,
    apply_netting: bool = True,
) -> dict[str, float]:
    """Sum PJ per category using the same path as demand extraction (with netting)."""
    df = year_df.copy()
    if "process_agg" in df.columns:
        df["agg_level_1"] = df["process_agg"]
    totals: dict[str, float] = {}
    for category, (var_type, filter_type, filter_values) in extraction_rules.items():
        filtered = _apply_extraction_rule(
            df, var_type, filter_type, filter_values, apply_netting=apply_netting
        )
        totals[category] = float(filtered["value"].sum()) if not filtered.empty else 0.0
    return totals


def export_reconciliation(
    tagged: pd.DataFrame,
    links: pd.DataFrame,
) -> pd.DataFrame:
    """
    Per PyPSA sector: soft-linked PJ that was *tagged* vs PJ actually *coloured*.

    This is the check that answers "are we forgetting a flow or double-counting
    one?" for the diagram itself. Everything else in the QA suite validates the
    extraction rules; nothing validated that the Sankey draws what the rules
    matched. The two numbers can legitimately differ a little — some tagged mass
    ends in a magenta ``U ·`` sink instead of a process→process ribbon — but a
    large or *positive* gap is a defect:

    - ``coloured < tagged``: a soft-link the diagram does not show (forgotten);
    - ``coloured > tagged``: energy coloured that no rule matched — untagged flow
      merged onto an exported process pair, or a fuel coloured twice along a
      gateway chain (double count).

    Returns one row per sector plus a ``TOTAL`` row.
    """
    cols = ["sector", "tagged_pj", "coloured_pj", "gap_pj", "coloured_share"]
    if tagged is None or tagged.empty or links is None or links.empty:
        return pd.DataFrame(columns=cols)

    energy = filter_energy_carrier_flows(tagged)
    exported_rows = energy[energy["exported"].fillna(False).astype(bool)]
    tagged_by_sector: dict[str, float] = {}
    for cats, value in zip(
        exported_rows["matched_categories"], exported_rows["value"]
    ):
        sector = link_sector(cats)
        if sector:
            tagged_by_sector[sector] = tagged_by_sector.get(sector, 0.0) + float(value)

    coloured = links[links["export_status"].isin(["exported", "double_count"])]
    coloured_by_sector = (
        coloured.groupby(coloured["export_sector"].astype(str))["value"].sum().to_dict()
        if "export_sector" in coloured.columns
        else {}
    )

    rows = []
    for sector in PYPSA_SECTOR_ORDER:
        t = float(tagged_by_sector.get(sector, 0.0))
        c = float(coloured_by_sector.get(sector, 0.0))
        if t == 0.0 and c == 0.0:
            continue
        rows.append(
            {
                "sector": sector,
                "tagged_pj": t,
                "coloured_pj": c,
                "gap_pj": c - t,
                "coloured_share": (c / t) if t > 1e-12 else float("nan"),
            }
        )
    t_all = sum(r["tagged_pj"] for r in rows)
    c_all = sum(r["coloured_pj"] for r in rows)
    rows.append(
        {
            "sector": "TOTAL",
            "tagged_pj": t_all,
            "coloured_pj": c_all,
            "gap_pj": c_all - t_all,
            "coloured_share": (c_all / t_all) if t_all > 1e-12 else float("nan"),
        }
    )
    return pd.DataFrame(rows, columns=cols)


def build_colored_sankey(
    links: pd.DataFrame,
    *,
    title: str,
    units: EnergyUnit = "twh",
) -> go.Figure:
    """Build a Plotly Sankey with typed node colours and export-status link colours."""
    if links.empty:
        fig = go.Figure()
        fig.update_layout(title_text=f"{title} (no data)")
        return fig

    label = unit_label(units)
    typed_nodes = collect_typed_nodes(links)
    node_index = {n["key"]: i for i, n in enumerate(typed_nodes)}
    has_kinds = {"source_kind", "target_kind"}.issubset(links.columns)

    colors = []
    customdata = []
    sources = []
    targets = []
    values = []
    for _, row in links.iterrows():
        v = pj_to_display(float(row["value"]), units)
        cats = str(row.get("matched_categories", "") or "")
        status = link_export_status(row)
        sector = str(row.get("export_sector", "") or "").strip()
        colors.append(link_color(status, sector))
        if has_kinds:
            sk = str(row["source_kind"])
            tk = str(row["target_kind"])
            sk_key = node_key(sk, str(row["source"]))
            tk_key = node_key(tk, str(row["target"]))
            tip = (
                f"{node_display_label(sk, row['source'])} → "
                f"{node_display_label(tk, row['target'])}<br>{v:.2f} {label}"
            )
        else:
            sk_key = str(row["source"])
            tk_key = str(row["target"])
            tip = f"{row['source']} → {row['target']}<br>{v:.2f} {label}"
        if cats and status == "context":
            tip += f"<br>Categories: {cats.replace('|', ', ')}"
        commodity = str(row.get("commodity", "") or "")
        if commodity:
            tip += f"<br>Commodity flow: {commodity.replace('|', ', ')}"
        tip += export_status_hover(
            status,
            cats,
            export_detail=str(row.get("export_detail", "") or ""),
            export_sector=sector,
        )
        customdata.append(tip)
        sources.append(node_index[sk_key])
        targets.append(node_index[tk_key])
        values.append(v)

    n_max = max(len(typed_nodes), 1)
    dyn_pad = max(4, min(20, int(300 / n_max)))
    dyn_thickness = max(10, min(30, int(600 / n_max)))

    fig = go.Figure(
        data=[
            go.Sankey(
                node=dict(
                    pad=dyn_pad,
                    thickness=dyn_thickness,
                    line=dict(color="rgba(40,40,40,0.65)", width=0.6),
                    label=[n["label"] for n in typed_nodes],
                    color=[n["color"] for n in typed_nodes],
                ),
                link=dict(
                    source=sources,
                    target=targets,
                    value=values,
                    color=colors,
                    customdata=customdata,
                    hovertemplate="%{customdata}<extra></extra>",
                ),
            )
        ]
    )
    fig.update_layout(
        title_text=title,
        font_size=10,
        height=700,
        annotations=[
            dict(
                text=(
                    "Nodes: process (green) · U · demand/imbalance residual (magenta). "
                    "Commodities are flows. Exported links coloured by PyPSA sector: "
                    "Industry (blue) · Transport (orange) · Residential (red) · "
                    "Services (teal) · Agriculture (brown). Grey = not exported; "
                    "purple = double-count (should not occur)."
                ),
                showarrow=False,
                xref="paper",
                yref="paper",
                x=0,
                y=-0.08,
                align="left",
                font=dict(size=11, color="#444"),
            )
        ],
        margin=dict(l=20, r=20, t=60, b=40),
    )
    return fig


def _html_table(df: pd.DataFrame, max_rows: int = 50) -> str:
    if df is None or df.empty:
        return "<p><em>No rows.</em></p>"
    show = df.head(max_rows)
    return show.to_html(index=False, float_format=lambda x: f"{x:.4g}")


def _resolve_qa_years(model: TimesAnnualFlows, year: int | list[int] | None) -> list[int]:
    if year is None:
        return model.years
    if isinstance(year, int):
        return [year]
    return sorted({int(y) for y in year})


def _write_qa_csvs_for_year(
    *,
    year: int,
    model: TimesAnnualFlows,
    metadata,
    rules: dict,
    tagged: pd.DataFrame,
    totals: dict[str, float],
    category_keys: dict,
    out_dir: Path,
    units: EnergyUnit = "twh",
    agg_level: str = "Aggregation Level 2",
) -> dict[str, Path | None]:
    """Write companion CSV diagnostics for one planning year."""
    energy_label = unit_label(units)
    flows_path = out_dir / f"qa_flows_{year}.csv"
    export_cols = [
        c
        for c in (
            "year",
            "region",
            "variable",
            "commodity_code",
            "commodity",
            "process_code",
            "process",
            "value",
            "sector",
            "process_agg",
            "pypsa_carrier",
            "exported",
        )
        if c in tagged.columns
    ]
    export_df = tagged[export_cols].copy()
    export_df["matched_categories"] = tagged["matched_categories"].map(
        lambda xs: "|".join(xs) if isinstance(xs, list) else str(xs)
    )
    export_df = prepare_energy_output(export_df, ["value"], units)
    export_df.to_csv(flows_path, index=False)

    coverage = pd.DataFrame(
        [
            {
                "category": cat,
                energy_label: pj_to_display(totals.get(cat, 0.0), units),
                "n_matched_keys": len(category_keys.get(cat, ())),
                "known_zero": cat in KNOWN_ZERO_CATEGORIES,
            }
            for cat in rules
        ]
    )
    coverage_path = out_dir / f"qa_export_coverage_{year}.csv"
    coverage.to_csv(coverage_path, index=False)

    pj_codes = set()
    if not metadata.mapping_df.empty and "unit" in metadata.mapping_df.columns:
        pj_codes = set(
            metadata.mapping_df.loc[
                metadata.mapping_df["unit"].astype(str).str.strip().str.upper() == "PJ",
                "times",
            ]
            .astype(str)
            .str.strip()
        )

    balance = commodity_balance_vs_comnet(
        model.flows, model.comnet, year=year, pj_commodity_codes=pj_codes or None
    )
    balance = prepare_energy_output(
        balance,
        ["fout", "fin", "net_flows", "comnet", "residual"],
        units,
    )
    balance_path = out_dir / f"qa_node_balance_{year}.csv"
    balance.to_csv(balance_path, index=False)

    netted_raw = net_bidirectional_links(
        tagged[
            [
                "year",
                "region",
                "variable",
                "commodity_code",
                "commodity",
                "process_code",
                "process",
                "value",
            ]
        ]
    )
    residuals = commodity_node_residuals(netted_raw)
    residuals = prepare_energy_output(
        residuals, ["inflow", "outflow", "residual"], units
    )
    residuals_path = out_dir / f"qa_commodity_residuals_{year}.csv"
    residuals.to_csv(residuals_path, index=False)

    loops = find_loop_components(netted_raw)
    loops_df = pd.DataFrame(
        [{"component_id": i, "nodes": " | ".join(comp)} for i, comp in enumerate(loops)]
    )
    if loops_df.empty:
        loops_df = pd.DataFrame(columns=["component_id", "nodes"])
    loops_path = out_dir / f"qa_loops_{year}.csv"
    loops_df.to_csv(loops_path, index=False)

    overlap = double_count_matrix(category_keys)
    overlap_path = out_dir / f"qa_double_count_{year}.csv"
    if overlap.empty:
        overlap = pd.DataFrame(
            columns=["category_a", "category_b", "n_overlap", "example_key"]
        )
    overlap.to_csv(overlap_path, index=False)

    parent_child = parent_child_sum_checks(totals)
    parent_child = prepare_energy_output(
        parent_child,
        ["parent_pj", "children_sum_pj", "residual_pj"],
        units,
    )
    parent_path = out_dir / f"qa_parent_child_{year}.csv"
    parent_child.to_csv(parent_path, index=False)

    empty_rules = empty_rule_report(totals, rules.keys())
    empty_rules = prepare_energy_output(empty_rules, ["pj"], units)
    empty_path = out_dir / f"qa_empty_rules_{year}.csv"
    empty_rules.to_csv(empty_path, index=False)

    topo_path: Path | None = out_dir / f"qa_topology_mismatches_{year}.csv"
    if model.topology_mismatches is not None and not model.topology_mismatches.empty:
        mm = model.topology_mismatches
        mm = mm[mm["year"] == year] if "year" in mm.columns else mm
        if not mm.empty:
            mm_out = mm.copy()
            if "value" in mm_out.columns:
                mm_out = prepare_energy_output(mm_out, ["value"], units)
            mm_out.to_csv(topo_path, index=False)
        elif topo_path.exists():
            topo_path.unlink()
            topo_path = None
    else:
        if topo_path.exists():
            topo_path.unlink()
        topo_path = None

    gap_all = tagged[
        (~tagged["exported"])
        & (tagged["sector"].isin(["RSD", "COM", "IND", "TRA", "AGR"]))
        & (tagged["variable"].str.upper() == "VAR_FIN")
    ]
    gap = gap_all
    if "process_type" in tagged.columns:
        gap = gap_all[gap_all["process_type"].astype(str).str.upper() == "DMD"]
    gap_sum = (
        gap.groupby(["sector", "process_agg", "pypsa_carrier"], dropna=False)["value"]
        .sum()
        .reset_index()
        .sort_values("value", ascending=False)
    )
    dmd_gap_pj = float(gap_sum["value"].sum()) if not gap_sum.empty else 0.0
    gap_sum = prepare_energy_output(gap_sum, ["value"], units)
    gap_path = out_dir / f"qa_coverage_gap_{year}.csv"
    gap_sum.to_csv(gap_path, index=False)
    gap_all_sum = (
        gap_all.groupby(["sector", "process_agg", "pypsa_carrier", "process_type"], dropna=False)[
            "value"
        ]
        .sum()
        .reset_index()
        .sort_values("value", ascending=False)
    )
    gap_all_sum = prepare_energy_output(gap_all_sum, ["value"], units)
    gap_all_path = out_dir / f"qa_coverage_gap_all_{year}.csv"
    gap_all_sum.to_csv(gap_all_path, index=False)

    nb_all = select_export_neighborhood(tagged)
    nb_out = prepare_energy_output(nb_all, ["value"], units)
    nb_path = out_dir / f"qa_export_neighborhood_{year}.csv"
    nb_out.to_csv(nb_path, index=False)

    # Sankey node → original TIMES process/commodity crosswalk (pre-netting inventory)
    label_map = build_sankey_label_map(tagged, agg_level, apply_netting=False)
    label_map = prepare_energy_output(label_map, ["value"], units)
    label_map_path = out_dir / f"qa_sankey_label_map_{year}.csv"
    label_map.to_csv(label_map_path, index=False)

    # ΣFOut/ΣFIn per process and per aggregated Sankey node: efficiencies, COPs,
    # and energy balances in one table.
    ratio_cols = [
        "inflow",
        "outflow",
        "balance",
        "exported_in",
        "exported_out",
        "unmapped_in",
        "unmapped_out",
    ]
    energy = filter_energy_carrier_flows(tagged)
    carrier_mask = pd.Series(tagged.index.isin(energy.index), index=tagged.index)
    activity_units = _process_activity_units(metadata.processes_df)

    ratios_proc = process_io_ratios(
        tagged,
        by="process_code",
        carrier_mask=carrier_mask,
        activity_units=activity_units,
    )
    ratios_proc = prepare_energy_output(ratios_proc, ratio_cols, units)
    ratios_proc_path = out_dir / f"qa_io_ratios_process_{year}.csv"
    ratios_proc.to_csv(ratios_proc_path, index=False)

    # Aggregated view: same ratio at the resolution the Sankey actually draws.
    # Aggregate the *energy-carrier* rows only — including activity outputs
    # (Mvkm, Mm², service units) here would make every end-use ratio meaningless.
    ratios_agg = process_io_ratios(
        aggregate_flows(energy, level=agg_level, apply_netting=False),
        by="process_code",
    )
    ratios_agg = ratios_agg.drop(
        columns=[
            c
            for c in ("activity_unit", "description", "sector", "unmapped_in", "unmapped_out")
            if c in ratios_agg.columns
        ]
    )
    ratios_agg = ratios_agg.rename(columns={"process_code": agg_level})
    ratios_agg = prepare_energy_output(ratios_agg, ratio_cols, units)
    ratios_agg_path = out_dir / f"qa_io_ratios_aggregated_{year}.csv"
    ratios_agg.to_csv(ratios_agg_path, index=False)

    return {
        "flows": flows_path,
        "coverage": coverage_path,
        "balance": balance_path,
        "residuals": residuals_path,
        "loops": loops_path,
        "double_count": overlap_path,
        "parent_child": parent_path,
        "empty_rules": empty_path,
        "coverage_gap": gap_path,
        "coverage_gap_all": gap_all_path,
        "export_neighborhood": nb_path,
        "sankey_label_map": label_map_path,
        "io_ratios_process": ratios_proc_path,
        "io_ratios_aggregated": ratios_agg_path,
        "topology_mismatches": topo_path,
        "empty_rules_df": empty_rules,
        "gap_sum": gap_sum,
        "balance_df": balance,
        "loops_df": loops_df,
        "overlap_df": overlap,
        "parent_child_df": parent_child,
        "coverage_df": coverage,
        "ratios_proc_df": ratios_proc,
        "ratios_agg_df": ratios_agg,
        "nb_all": nb_all,
        "dmd_gap_pj": dmd_gap_pj,
    }


def generate_qa_report(
    vd_file: Path | str,
    out_dir: Path | str,
    year: int | list[int] | None = None,
    *,
    vdt_file: Path | str | None = None,
    mappings_dir: Path | str | None = None,
    config: PipelineConfig | None = None,
    flow_threshold_l0: float = 0.5,
    flow_threshold_l1: float = 0.5,
    flow_threshold_export: float | None = None,
    units: EnergyUnit = "twh",
    model: TimesAnnualFlows | None = None,
    agg_level: str = "Aggregation Level 2",
) -> dict[str, Path]:
    """
    Write multi-view QA HTML + companion CSV tables.

    When ``year`` is omitted, all years present in the model are processed.
    Energy values in HTML, CSVs, and Sankeys use ``units`` (default: TWh).
    ``flow_threshold_export`` is interpreted in the selected display unit.

    Returns a dict of artifact name → path.
    """
    del flow_threshold_l0, flow_threshold_l1  # kept for CLI compatibility
    if flow_threshold_export is None:
        flow_threshold_export = default_flow_threshold(units)
    flow_threshold_pj = display_to_pj(flow_threshold_export, units)
    energy_label = unit_label(units)
    config = config or PipelineConfig()
    if agg_level == "Aggregation Level 2" and config.agg_level != "Aggregation Level 2":
        agg_level = config.agg_level
    mappings_dir = Path(mappings_dir or default_mappings_dir())
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    if model is None:
        model = load_times_annual_flows(
            vd_file,
            mappings_dir,
            vdt_file=vdt_file,
            config=config,
        )

    years = _resolve_qa_years(model, year)
    if not years:
        logger.warning("No years available in model")
        return {}

    metadata = load_metadata(mappings_dir)
    rules = load_extraction_rules(metadata.extraction_rules_file)
    process_units = _process_activity_units(metadata.processes_df)

    year_payloads: dict[int, dict] = {}
    all_artifacts: dict[str, Path] = {}
    category_candidates: dict[str, float] = {}

    for yr in years:
        year_flows = model.energy_flows(yr)
        if year_flows.empty:
            logger.warning("No energy flows for year %d — skipping", yr)
            continue

        tagged = tag_flows_with_rules(year_flows, rules, apply_netting=False)
        category_keys = tagged.attrs.get("category_keys", {})
        totals = category_pj_totals(year_flows, rules, apply_netting=True)

        csv_info = _write_qa_csvs_for_year(
            year=yr,
            model=model,
            metadata=metadata,
            rules=rules,
            tagged=tagged,
            totals=totals,
            category_keys=category_keys,
            out_dir=out_dir,
            units=units,
            agg_level=agg_level,
        )
        for key, path in csv_info.items():
            if isinstance(path, Path):
                all_artifacts[f"{key}_{yr}"] = path

        for cat, pj in totals.items():
            if pj > 0:
                category_candidates[cat] = max(category_candidates.get(cat, 0.0), pj)

        year_payloads[yr] = {
            "tagged": tagged,
            "totals": totals,
            "csv_info": csv_info,
        }

    active_years = sorted(year_payloads.keys())
    if not active_years:
        logger.warning("No energy flows for requested years")
        return {}

    # Only the categories that will appear in the HTML (top 15 by PJ).
    top_categories = sorted(category_candidates.items(), key=lambda x: -x[1])[:15]
    top_cat_names = {cat for cat, _pj in top_categories}

    # --- Interactive Sankey datasets (all years × netted/gross) ---
    system_netted: dict[int, list] = {}
    system_gross: dict[int, list] = {}
    system_nodes: dict[int, list] = {}
    category_series: dict[str, dict[str, dict[int, list]]] = {}

    for yr in active_years:
        tagged = year_payloads[yr]["tagged"]
        totals = year_payloads[yr]["totals"]

        # One aggregation per (year, netting), reused by the system diagram and by
        # every per-category neighbourhood as their reference view.
        system_agg = {
            netting: aggregate_system_flows(
                tagged, agg_level=agg_level, apply_netting=netting
            )
            for netting in (True, False)
        }

        # Core masks for all category views in one pass over matched_categories,
        # instead of one full-column scan per category.
        cat_indices: dict[str, list] = {cat: [] for cat in top_cat_names}
        for idx, cats in zip(tagged.index, tagged["matched_categories"]):
            if not isinstance(cats, list):
                continue
            for cat in cats:
                if cat in cat_indices:
                    cat_indices[cat].append(idx)

        year_system_links = []
        for apply_netting, store in ((True, system_netted), (False, system_gross)):
            links = prepare_system_sankey_links(
                tagged,
                flow_threshold=flow_threshold_pj,
                apply_netting=apply_netting,
                agg_level=agg_level,
                process_activity_units=process_units,
                system_agg=system_agg[apply_netting],
            )
            store[yr] = links_to_records(links, units=units)
            year_system_links.append(links)
        system_nodes[yr] = collect_typed_nodes(
            pd.concat(year_system_links, ignore_index=True) if year_system_links else pd.DataFrame()
        )

        # Does the diagram colour what the rules tagged? (netted view = the default)
        recon = export_reconciliation(tagged, year_system_links[0])
        anchor_ledger = pd.DataFrame(
            year_system_links[0].attrs.get("anchor_ledger", [])
        )
        for frame, name, cols in (
            (recon, "export_reconciliation", ["tagged_pj", "coloured_pj", "gap_pj"]),
            (anchor_ledger, "anchor_ledger", ["exported_fin_pj", "anchorable_out_pj"]),
        ):
            path = out_dir / f"qa_{name}_{yr}.csv"
            prepare_energy_output(frame, cols, units).to_csv(path, index=False)
            all_artifacts[f"{name}_{yr}"] = path
        year_payloads[yr]["recon"] = prepare_energy_output(
            recon, ["tagged_pj", "coloured_pj", "gap_pj"], units
        )
        year_payloads[yr]["anchor_ledger"] = prepare_energy_output(
            anchor_ledger, ["exported_fin_pj", "anchorable_out_pj"], units
        )

        for cat in top_cat_names:
            pj = float(totals.get(cat, 0.0))
            if pj <= 0 or not cat_indices[cat]:
                continue
            core_mask = pd.Series(False, index=tagged.index)
            core_mask.loc[cat_indices[cat]] = True
            cat_id = cat.replace(" ", "-").replace("/", "-").lower()
            if cat_id not in category_series:
                category_series[cat_id] = {
                    "title": cat,
                    "netted": {},
                    "gross": {},
                    "nodes": {},
                }
            year_cat_links = []
            for apply_netting, variant in ((True, "netted"), (False, "gross")):
                links = prepare_export_sankey_links(
                    tagged,
                    core_mask=core_mask,
                    flow_threshold=max(0.1, flow_threshold_pj * 0.25),
                    apply_netting=apply_netting,
                    agg_level=agg_level,
                    process_activity_units=process_units,
                    reference_agg=system_agg[apply_netting],
                )
                category_series[cat_id][variant][yr] = links_to_records(
                    links, units=units
                )
                year_cat_links.append(links)
            category_series[cat_id]["nodes"][yr] = collect_typed_nodes(
                pd.concat(year_cat_links, ignore_index=True) if year_cat_links else pd.DataFrame()
            )

    charts: list[dict] = [
        build_sankey_dataset(
            chart_id="system",
            title="A. Whole TIMES energy flows",
            years=active_years,
            netted_by_year=system_netted,
            gross_by_year=system_gross,
            nodes_by_year=system_nodes,
            units=units,
            subtitle=(
                "All energy-carrier flows aggregated to "
                f"<em>{agg_level}</em>. Commodity hubs are collapsed to "
                "<strong>process→process</strong> flows (commodity name on hover). "
                "Expected final-demand / non-PJ sinks become magenta <strong>U ·</strong> "
                "nodes named after the demand process (hover explains). "
                "Only unexplained imbalances keep an <em>Unbalanced …</em> label and "
                "are logged as errors. "
                "Process nodes are green; exported links are coloured by PyPSA "
                "<strong>sector</strong> — Industry (blue), Transport (orange), "
                "Residential (red), Services (teal), Agriculture (brown); grey = "
                "not exported. Soft-linked fuel inputs are anchored to the demand "
                "inflow (e.g. industry fuel exports on the <em>Fuel Tech (IND) → "
                "Industry</em> links). Coloured PJ is path energy after "
                "collapse/netting — some residual export mass sits in magenta U · "
                "sinks rather than coloured links."
            ),
        ),
    ]

    for idx, (cat, _pj) in enumerate(top_categories):
        cat_id = cat.replace(" ", "-").replace("/", "-").lower()
        series = category_series.get(cat_id)
        if series is None:
            continue
        charts.append(
            build_sankey_dataset(
                chart_id=f"category-{idx}",
                title=f"B{idx + 1}. {cat} — export neighbourhood",
                years=active_years,
                netted_by_year=series["netted"],
                gross_by_year=series["gross"],
                nodes_by_year=series["nodes"],
                units=units,
                subtitle=(
                    "Matched flows plus n−1/n+1 context for this one PyPSA demand "
                    "category. Coloured links are the soft-linked flows this "
                    "category exports; magenta U · nodes are producers/consumers "
                    "the ±1-hop truncation left out, not TIMES errors."
                ),
            )
        )

    latest = active_years[-1]
    latest_info = year_payloads[latest]["csv_info"]
    empty_rules = latest_info["empty_rules_df"]
    gap_sum = latest_info["gap_sum"]
    balance = latest_info["balance_df"]
    loops_df = latest_info["loops_df"]
    overlap = latest_info["overlap_df"]
    parent_child = latest_info["parent_child_df"]
    coverage = latest_info["coverage_df"]
    ratios_proc = latest_info["ratios_proc_df"]
    ratios_agg = latest_info["ratios_agg_df"]
    recon = year_payloads[latest].get("recon", pd.DataFrame())
    anchor_ledger = year_payloads[latest].get("anchor_ledger", pd.DataFrame())
    tagged_latest = year_payloads[latest]["tagged"]
    nb_all = latest_info["nb_all"]

    recon_gap = 0.0
    if not recon.empty:
        total_row = recon[recon["sector"] == "TOTAL"]
        gap_col = [c for c in recon.columns if c.startswith("gap")]
        if not total_row.empty and gap_col:
            recon_gap = float(total_row.iloc[0][gap_col[0]])

    dmd_gap_pj = float(latest_info.get("dmd_gap_pj", 0.0))
    exported_pj = float(tagged_latest.loc[tagged_latest["exported"], "value"].sum())
    adequacy = (
        "PARTIALLY ADEQUATE"
        if dmd_gap_pj > 5.0 or int((~empty_rules["ok"]).sum()) > 0
        else "ADEQUATE (no large DMD gaps / empty rules)"
    )

    year_span = (
        str(active_years[0])
        if len(active_years) == 1
        else f"{active_years[0]}–{active_years[-1]}"
    )
    header_html = f"""
<h1>TIMES Extraction QA — {year_span}</h1>
<p class='meta'>VD: {vd_file}{f" | VDT: {vdt_file}" if vdt_file else ""} | mappings: {mappings_dir} | units: {energy_label}</p>
<div class='verdict'>
  <strong>Extraction-rule verdict ({latest}):</strong> {adequacy}<br>
  Former aggregation CSVs remain active
  (<code>mapping_processes.csv</code> Aggregation Level 2 = <code>process_agg</code>,
  <code>mapping_commodities.csv</code> PyPSA Energy Carrier,
  <code>extraction_rules.csv</code>).
  Sankey views use those CSV labels; export neighbourhoods add n−1/n+1 context.
  Each diagram has a year timeline and a netting toggle.
</div>
<div class='legend'><strong>Exported link colours (by PyPSA sector):</strong>
  {"".join(f"<span style='background:{PYPSA_SECTOR_COLORS[s]};color:#fff'>{s}</span>" for s in PYPSA_SECTOR_ORDER)}
  <span style='background:{CONTEXT_COLOR}'>Not exported</span>
  <span style='background:{DOUBLE_COUNT_COLOR};color:#fff'>Double-count (should not occur)</span>
</div>
<p class='meta'>Soft-linked fuel inputs are anchored to the demand inflow: e.g. industry
fuel exports appear on the <em>Fuel Tech (IND) → Industry</em> links, not on the
upstream import/power-plant links.</p>
<h2>Summary ({latest})</h2>
<ul>
  <li>Years in report: {", ".join(str(y) for y in active_years)}</li>
  <li>Energy flow rows: {len(tagged_latest)}</li>
  <li>Exported flow rows: {int(tagged_latest["exported"].sum())}
    ({100 * tagged_latest["exported"].mean():.1f}%)</li>
  <li>Export-neighbourhood rows (matched + n−1 + n+1): {len(nb_all)}</li>
  <li>Exported energy (gross tagged, pre-netting):
    {format_energy(exported_pj, units)}</li>
  <li>DMD coverage-gap (demand-side <code>VAR_FIn</code> in no rule):
    {format_energy(dmd_gap_pj, units)}</li>
  <li>Sankey vs rules — coloured minus tagged: {recon_gap:+.2f} {energy_label}
    (see <em>Soft-link reconciliation</em>; positive = coloured energy no rule
    matched)</li>
  <li>Disallowed double-count pairs: {len(overlap)}
    (parent/child hierarchies excluded)</li>
  <li>Unexpected empty rules: {int((~empty_rules["ok"]).sum())}</li>
  <li>Commodity balance failures (|residual|&gt;tol):
    {int((~balance["ok"]).sum()) if not balance.empty else "n/a"}</li>
  <li>Loop components after netting: {len(loops_df)}</li>
</ul>
<h2>Interactive Sankey diagrams</h2>
<p>Use the year slider and <em>Net bidirectional flows</em> checkbox on each chart.
Companion CSVs are written per year as <code>qa_*_{{year}}.csv</code>
(including <code>qa_sankey_label_map_{{year}}.csv</code> to map Sankey labels
back to TIMES process/commodity codes).</p>
"""

    footer_html = f"""
<h2>Inflow / outflow ratios ({latest})</h2>
<p>ΣVAR_FOut ÷ ΣVAR_FIn per process. One table, three readings:
<strong>efficiency</strong> (boilers, power plants: 0.3–1.0),
<strong>COP</strong> (heat pumps and TIMES service accounting: &gt; 1), and
<strong>energy balance</strong> (<code>balance = outflow − inflow</code> should be
≤ 0 for any real conversion). Only flows that pass the Sankey energy-carrier
filter are counted; <code>unmapped_in</code> / <code>unmapped_out</code> report
the {energy_label} dropped for lack of a commodity mapping — a non-zero
<code>unmapped_in</code> is the usual reason a ratio looks impossible.
<code>exported_in</code> / <code>exported_out</code> are the soft-linked share, so
this table also shows where each PyPSA demand is picked off.</p>

<h3>Aggregated processes (<em>{agg_level}</em> — the Sankey's own nodes)</h3>
{_html_table(ratios_agg, max_rows=200)}

<h3>Individual TIMES processes — top 60 by throughput</h3>
<p>Full table: <code>qa_io_ratios_process_{latest}.csv</code>
(aggregated: <code>qa_io_ratios_aggregated_{latest}.csv</code>).</p>
{_html_table(ratios_proc, max_rows=60)}

<h2>Soft-link coverage ({latest})</h2>
<h3>Reconciliation — does the Sankey colour what the rules tagged?</h3>
<p>Per sector: <code>tagged</code> is the soft-linked energy the extraction rules
matched; <code>coloured</code> is what the diagram actually draws as an exported
ribbon. A small negative gap is expected (some tagged mass lands in a magenta
<strong>U ·</strong> sink instead of a process→process ribbon). A <strong>positive</strong>
gap means energy is coloured that no rule matched — an untagged flow merged onto
an exported process pair, or a fuel coloured twice along a chain.</p>
{_html_table(recon)}
<h3>Anchoring ledger — where soft-linked fuel inputs were moved</h3>
<p>Each fuel-delivery gateway whose <code>VAR_FIn</code> is soft-linked. The
highlight is moved from the upstream fuel-supply link onto the gateway's demand
inflows <em>only</em> when the outputs carry the same energy
(<code>ratio</code> ≈ 1). <code>anchored=False</code> means the highlight was
deliberately left upstream so no coloured energy is invented or lost — expected
for lossy or multi-purpose processes (electrolysers, storage).</p>
{_html_table(anchor_ledger, max_rows=60)}
<h3>Exported energy per PyPSA category</h3>
{_html_table(coverage, max_rows=200)}
<h3>Coverage gaps — demand-sector <code>VAR_FIn</code> matched by no rule</h3>
<p>Most of this is legitimately excluded downstream consumption (n+1) of
production already exported one hop upstream — exporting both would double-count.
Intermediate PRE / fuel-tech gaps are in
<code>qa_coverage_gap_all_{latest}.csv</code>.</p>
{_html_table(gap_sum.head(40))}

<h2>Consistency checks ({latest})</h2>
<details>
<summary>Double-count, parent–child, commodity balance, loops, topology
(expand — these are pass/fail checks, each with a companion CSV)</summary>
<h3>Disallowed double-count overlaps</h3>
<p>Empty is the expected result: parent/child category hierarchies are
allowlisted, anything else listed here is a real double count.</p>
{_html_table(overlap)}
<h3>Parent–child sum checks</h3>
{_html_table(parent_child)}
<h3>Empty / known-zero rules</h3>
{_html_table(empty_rules)}
<h3>Commodity balance vs VAR_Comnet (failures first)</h3>
{_html_table(balance.sort_values("ok").head(40) if not balance.empty else balance)}
<h3>Loop components</h3>
{_html_table(loops_df)}
<h3>Topology mismatches</h3>
{
    _html_table(model.topology_mismatches.head(40))
    if model.topology_mismatches is not None and not model.topology_mismatches.empty
    else "<p><em>No topology file or no mismatches.</em></p>"
}
</details>
"""

    html_path = out_dir / "qa_report.html"
    html_path.write_text(
        assemble_interactive_report_html(
            page_title=f"TIMES Extraction QA — {year_span}",
            header_html=header_html,
            charts=charts,
            footer_html=footer_html,
        ),
        encoding="utf-8",
    )
    logger.info("Wrote QA report to %s", html_path)

    all_artifacts["report"] = html_path
    return all_artifacts
