"""Multi-view Sankey QA report with soft-link export highlighting."""

from __future__ import annotations

import colorsys
import logging
from pathlib import Path

import pandas as pd
import plotly.graph_objects as go

from times_pypsa.aggregation import aggregate_flows, sankey_links_from_flows
from times_pypsa.balances import (
    KNOWN_ZERO_CATEGORIES,
    commodity_balance_vs_comnet,
    commodity_node_residuals,
    double_count_matrix,
    empty_rule_report,
    find_loop_components,
    flow_key,
    parent_child_sum_checks,
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

logger = logging.getLogger(__name__)

EXPORTED_COLOR = "rgba(31, 119, 180, 0.85)"
CONTEXT_COLOR = "rgba(160, 160, 160, 0.45)"
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
        df = df.loc[has_carrier & ~is_emission].copy()
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


def prepare_export_sankey_links(
    tagged: pd.DataFrame,
    *,
    core_mask: pd.Series | None = None,
    flow_threshold: float = 1.0,
    apply_netting: bool = True,
) -> pd.DataFrame:
    """
    Build Sankey links for the export neighbourhood using mapping CSV labels only.

    Aggregation: process_agg (Aggregation Level 2) × pypsa_carrier.
    """
    nb = select_export_neighborhood(tagged, core_mask=core_mask)
    if nb.empty:
        return pd.DataFrame(
            columns=["source", "target", "value", "exported", "matched_categories"]
        )
    agg = aggregate_flows(nb, level="mapping", apply_netting=apply_netting)
    return sankey_links_from_flows(agg, flow_threshold=flow_threshold)


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
    """
    if year_df.empty:
        return year_df

    df = year_df.copy()
    # Rules historically filter on agg_level_1 holding Aggregation Level 2 labels.
    if "process_agg" in df.columns:
        df["agg_level_1"] = df["process_agg"]
    elif "agg_level_1_for_rules" in df.columns:
        df["agg_level_1"] = df["agg_level_1_for_rules"]

    # Map original index → list of categories
    matches: dict[int, list[str]] = {i: [] for i in df.index}
    category_keys: dict[str, set[tuple]] = {}

    for category, (var_type, filter_type, filter_values) in extraction_rules.items():
        filtered = _apply_extraction_rule(
            df, var_type, filter_type, filter_values, apply_netting=apply_netting
        )
        keys: set[tuple] = set()
        if not filtered.empty:
            # Without netting, filtered index aligns with df
            for idx in filtered.index:
                if idx in matches:
                    matches[idx].append(category)
            for _, row in filtered.iterrows():
                keys.add(flow_key(row))
        category_keys[category] = keys

    df["matched_categories"] = df.index.map(
        lambda i: matches.get(i, [])
    )
    df["exported"] = df["matched_categories"].map(lambda xs: bool(xs))
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


def build_colored_sankey(
    links: pd.DataFrame,
    *,
    title: str,
    color_mode: str = "export",
    category_color_map: dict[str, str] | None = None,
) -> go.Figure:
    """
    Build a Plotly Sankey with link colors.

    color_mode:
      - export: blue if exported else grey
      - category: color by first matched category
      - plain: single blue
    """
    if links.empty:
        fig = go.Figure()
        fig.update_layout(title_text=f"{title} (no data)")
        return fig

    nodes = pd.concat([links["source"], links["target"]]).unique().tolist()
    node_index = {n: i for i, n in enumerate(nodes)}

    colors = []
    customdata = []
    for _, row in links.iterrows():
        v = float(row["value"])
        cats = str(row.get("matched_categories", "") or "")
        exported = bool(row.get("exported", False))
        if color_mode == "export":
            colors.append(EXPORTED_COLOR if exported else CONTEXT_COLOR)
        elif color_mode == "category" and category_color_map and cats:
            first = cats.split("|")[0]
            colors.append(category_color_map.get(first, EXPORTED_COLOR))
        else:
            colors.append(EXPORTED_COLOR)
        tip = f"{row['source']} → {row['target']}<br>{v:.2f} PJ"
        if cats:
            tip += f"<br>Categories: {cats.replace('|', ', ')}"
        elif color_mode == "export":
            tip += "<br>Neighbourhood context (not exported)"
        customdata.append(tip)

    n_max = max(len(nodes), 1)
    dyn_pad = max(4, min(20, int(300 / n_max)))
    dyn_thickness = max(10, min(30, int(600 / n_max)))

    fig = go.Figure(
        data=[
            go.Sankey(
                node=dict(
                    pad=dyn_pad,
                    thickness=dyn_thickness,
                    line=dict(color="black", width=0.5),
                    label=[str(n) for n in nodes],
                ),
                link=dict(
                    source=links["source"].map(node_index).tolist(),
                    target=links["target"].map(node_index).tolist(),
                    value=links["value"].tolist(),
                    color=colors,
                    customdata=customdata,
                    hovertemplate="%{customdata}<extra></extra>",
                ),
            )
        ]
    )
    fig.update_layout(title_text=title, font_size=10, height=700)
    return fig


def _html_table(df: pd.DataFrame, max_rows: int = 50) -> str:
    if df is None or df.empty:
        return "<p><em>No rows.</em></p>"
    show = df.head(max_rows)
    return show.to_html(index=False, float_format=lambda x: f"{x:.4g}")


def generate_qa_report(
    vd_file: Path | str,
    out_dir: Path | str,
    year: int,
    *,
    vdt_file: Path | str | None = None,
    mappings_dir: Path | str | None = None,
    config: PipelineConfig | None = None,
    flow_threshold_l0: float = 0.5,
    flow_threshold_l1: float = 0.5,
    flow_threshold_export: float = 1.0,
    model: TimesAnnualFlows | None = None,
) -> dict[str, Path]:
    """
    Write multi-view QA HTML + companion CSV tables for one year.

    Returns a dict of artifact name → path.
    """
    config = config or PipelineConfig()
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

    metadata = load_metadata(mappings_dir)
    rules = load_extraction_rules(metadata.extraction_rules_file)

    year_flows = model.energy_flows(year)
    if year_flows.empty:
        logger.warning("No energy flows for year %d", year)
        return {}

    tagged = tag_flows_with_rules(year_flows, rules, apply_netting=False)
    category_keys = tagged.attrs.get("category_keys", {})
    totals = category_pj_totals(year_flows, rules, apply_netting=True)

    # --- CSVs ---
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
    export_df.to_csv(flows_path, index=False)

    coverage = pd.DataFrame(
        [
            {
                "category": cat,
                "pj": totals.get(cat, 0.0),
                "twh": totals.get(cat, 0.0) * 0.277778,
                "n_matched_keys": len(category_keys.get(cat, ())),
                "known_zero": cat in KNOWN_ZERO_CATEGORIES,
            }
            for cat in rules
        ]
    )
    coverage_path = out_dir / f"qa_export_coverage_{year}.csv"
    coverage.to_csv(coverage_path, index=False)

    # PJ commodity codes from mapping
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
    parent_path = out_dir / f"qa_parent_child_{year}.csv"
    parent_child.to_csv(parent_path, index=False)

    empty_rules = empty_rule_report(totals, rules.keys())
    empty_path = out_dir / f"qa_empty_rules_{year}.csv"
    empty_rules.to_csv(empty_path, index=False)

    topo_path = out_dir / f"qa_topology_mismatches_{year}.csv"
    if model.topology_mismatches is not None and not model.topology_mismatches.empty:
        mm = model.topology_mismatches
        mm = mm[mm["year"] == year] if "year" in mm.columns else mm
        if not mm.empty:
            mm.to_csv(topo_path, index=False)
        elif topo_path.exists():
            topo_path.unlink()
            topo_path = None
        else:
            topo_path = None
    else:
        if topo_path.exists():
            topo_path.unlink()
        topo_path = None

    # Coverage gap: end-use (DMD) demand-sector flows not matched by any rule.
    # Intermediate PRE/Fuel-Tech flows are often upstream of extracted demands
    # and would false-positive as "missing".
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
    gap_path = out_dir / f"qa_coverage_gap_{year}.csv"
    gap_sum.to_csv(gap_path, index=False)
    gap_all_path = out_dir / f"qa_coverage_gap_all_{year}.csv"
    (
        gap_all.groupby(["sector", "process_agg", "pypsa_carrier", "process_type"], dropna=False)[
            "value"
        ]
        .sum()
        .reset_index()
        .sort_values("value", ascending=False)
        .to_csv(gap_all_path, index=False)
    )

    # --- Sankey views (mapping CSV labels only; export neighbourhood) ---
    cat_colors = {cat: _category_color(i) for i, cat in enumerate(rules)}

    nb_all = select_export_neighborhood(tagged)
    links_export = prepare_export_sankey_links(
        tagged, flow_threshold=flow_threshold_export
    )
    fig_export = build_colored_sankey(
        links_export,
        title=(
            f"A. PyPSA export neighbourhood — {year} (PJ) "
            f"[blue=exported, grey=n−1/n+1 context; "
            f"labels=Aggregation Level 2 × PyPSA carrier]"
        ),
        color_mode="export",
    )
    nb_path = out_dir / f"qa_export_neighborhood_{year}.csv"
    nb_all.to_csv(nb_path, index=False)

    # View B: per-category neighbourhood (matched + n−1 + n+1)
    category_figs: list[tuple[str, go.Figure]] = []
    for cat, pj in sorted(totals.items(), key=lambda x: -x[1]):
        if pj <= 0:
            continue
        core_mask = tagged["matched_categories"].map(
            lambda xs, c=cat: isinstance(xs, list) and c in xs
        )
        if not core_mask.any():
            continue
        links = prepare_export_sankey_links(
            tagged,
            core_mask=core_mask,
            flow_threshold=max(0.1, flow_threshold_export * 0.25),
        )
        # Prefer category colour on exported links; context stays grey via export mode
        # Re-tag matched_categories on links for hover when possible
        fig = build_colored_sankey(
            links,
            title=(
                f"B. {cat} — {pj:.2f} PJ exported ({year}) "
                f"[neighbourhood = matched + n−1 + n+1]"
            ),
            color_mode="export",
            category_color_map=cat_colors,
        )
        category_figs.append((cat, fig))
        if len(category_figs) >= 15:
            break

    dmd_gap_pj = float(gap_sum["value"].sum()) if not gap_sum.empty else 0.0
    adequacy = (
        "PARTIALLY ADEQUATE"
        if dmd_gap_pj > 5.0 or int((~empty_rules["ok"]).sum()) > 0
        else "ADEQUATE (no large DMD gaps / empty rules)"
    )

    # --- Assemble HTML ---
    html_path = out_dir / f"qa_report_{year}.html"
    parts: list[str] = [
        "<!DOCTYPE html><html><head><meta charset='utf-8'>",
        f"<title>TIMES Extraction QA — {year}</title>",
        "<style>",
        "body{font-family:system-ui,sans-serif;margin:24px;max-width:1400px}",
        "h1,h2{color:#222} .meta{color:#555} table{border-collapse:collapse;font-size:13px}",
        "th,td{border:1px solid #ccc;padding:4px 8px} th{background:#f0f0f0}",
        ".ok{color:green}.bad{color:#b00} .legend span{display:inline-block;padding:2px 8px;margin:2px;border-radius:3px}",
        ".verdict{padding:12px 16px;background:#f7f7f7;border-left:4px solid #1f77b4;margin:16px 0}",
        "</style></head><body>",
        f"<h1>TIMES Extraction QA — {year}</h1>",
        f"<p class='meta'>VD: {vd_file}"
        + (f" | VDT: {vdt_file}" if vdt_file else "")
        + f" | mappings: {mappings_dir}</p>",
        "<div class='verdict'>",
        f"<strong>Extraction-rule verdict:</strong> {adequacy}<br>",
        "Former aggregation CSVs remain active "
        "(<code>mapping_processes.csv</code> Aggregation Level 2 = <code>process_agg</code>, "
        "<code>mapping_commodities.csv</code> PyPSA Energy Carrier, "
        "<code>extraction_rules.csv</code>). "
        "No new aggregation CSV formalism. Sankey labels use those CSV fields only; "
        "diagrams show exported flows plus n−1/n+1 neighbourhood context.",
        "</div>",
        "<div class='legend'><strong>Legend:</strong> "
        f"<span style='background:{EXPORTED_COLOR};color:#fff'>Exported to pypsa-wal</span> "
        f"<span style='background:{CONTEXT_COLOR}'>Neighbourhood context (n−1 / n+1)</span></div>",
        "<h2>Summary</h2>",
        "<ul>",
        f"<li>Energy flow rows: {len(tagged)}</li>",
        f"<li>Exported flow rows: {int(tagged['exported'].sum())} "
        f"({100*tagged['exported'].mean():.1f}%)</li>",
        f"<li>Export-neighbourhood rows (matched + n−1 + n+1): {len(nb_all)}</li>",
        f"<li>Exported PJ (gross tagged, pre-netting): "
        f"{tagged.loc[tagged['exported'], 'value'].sum():.1f}</li>",
        f"<li>Sankey links after mapping aggregation (threshold "
        f"{flow_threshold_export} PJ): {len(links_export)}</li>",
        f"<li>Commodity balance failures (|residual|&gt;tol): "
        f"{int((~balance['ok']).sum()) if not balance.empty else 'n/a'}</li>",
        f"<li>Unexpected empty rules: "
        f"{int((~empty_rules['ok']).sum())}</li>",
        f"<li>Disallowed double-count pairs: {len(overlap)}</li>",
        f"<li>Loop components after netting: {len(loops)}</li>",
        f"<li>DMD coverage-gap PJ (not in any rule): {dmd_gap_pj:.1f}</li>",
        "</ul>",
        "<h2>A. PyPSA export neighbourhood</h2>",
        "<p>Processes labeled by <em>Aggregation Level 2</em>; commodities by "
        "<em>PyPSA Energy Carrier</em> (existing mapping CSVs). "
        "Blue = flows matched by extraction rules; grey = one-hop upstream/downstream "
        "context sharing those processes or commodities.</p>",
        fig_export.to_html(full_html=False, include_plotlyjs="cdn"),
        "<h2>B. Category neighbourhoods (exported + n−1 + n+1)</h2>",
    ]
    for cat, fig in category_figs:
        parts.append(f"<h3>{cat}</h3>")
        parts.append(fig.to_html(full_html=False, include_plotlyjs=False))

    parts.extend(
        [
            "<h2>C. Diagnostics</h2>",
            "<h3>Export coverage</h3>",
            _html_table(coverage),
            "<h3>Empty / known-zero rules</h3>",
            _html_table(empty_rules),
            "<h3>Parent–child sum checks</h3>",
            _html_table(parent_child),
            "<h3>Disallowed double-count overlaps</h3>",
            _html_table(overlap),
            "<h3>Commodity balance vs VAR_Comnet (failures first)</h3>",
            _html_table(
                balance.sort_values("ok").head(40) if not balance.empty else balance
            ),
            "<h3>Loop components</h3>",
            _html_table(loops_df),
            "<h3>Coverage gaps (DMD demand-sector VAR_FIn not in any rule)</h3>",
            "<p>Intermediate PRE/Fuel-Tech gaps are in "
            f"<code>qa_coverage_gap_all_{year}.csv</code>.</p>",
            _html_table(gap_sum.head(40)),
            "<h3>Topology mismatches</h3>",
            (
                _html_table(model.topology_mismatches.head(40))
                if model.topology_mismatches is not None
                and not model.topology_mismatches.empty
                else "<p><em>No topology file or no mismatches.</em></p>"
            ),
            "</body></html>",
        ]
    )
    html_path.write_text("\n".join(parts), encoding="utf-8")
    logger.info("Wrote QA report to %s", html_path)

    artifacts = {
        "report": html_path,
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
    }
    if topo_path is not None:
        artifacts["topology_mismatches"] = topo_path
    return artifacts
