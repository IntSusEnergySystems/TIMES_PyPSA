"""Energy-balance, loop, coverage, and double-count checks for TIMES QA."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Iterable

import pandas as pd

from times_pypsa.aggregation import sankey_links_from_flows
from times_pypsa.pipeline import RuleMetadata, bundled_rule_metadata

logger = logging.getLogger(__name__)

_RULE_METADATA = bundled_rule_metadata()

#: Categories whose ``expect`` column declares them structurally zero.
KNOWN_ZERO_CATEGORIES = frozenset(
    cat for cat, meta in _RULE_METADATA.items() if meta.expect == "zero"
)


def _parent_child_map(metadata: dict[str, RuleMetadata]) -> dict[str, frozenset[str]]:
    """Parent → children, from the ``parent`` column of the extraction rules."""
    children: dict[str, set[str]] = defaultdict(set)
    for category, meta in metadata.items():
        if meta.parent:
            children[meta.parent].add(category)
    return {parent: frozenset(kids) for parent, kids in children.items()}


#: Parent → children: overlaps allowed because the child is a subset of the parent.
ALLOWED_OVERLAPS: dict[str, frozenset[str]] = _parent_child_map(_RULE_METADATA)


def commodity_balance_vs_comnet(
    flows: pd.DataFrame,
    comnet: pd.DataFrame,
    *,
    year: int,
    tolerance_pj: float = 1e-3,
    pj_commodity_codes: set[str] | None = None,
    require_flow_activity: bool = True,
) -> pd.DataFrame:
    """
    Compare Σ VAR_FOut − Σ VAR_FIn to VAR_Comnet for each commodity.

    Returns a residual table with columns:
        commodity_code, fout, fin, net_flows, comnet, residual, ok

    When ``require_flow_activity`` is True (default), commodities with no
    FIn/FOut (typical for emission aggregates only present in VAR_Comnet)
    are omitted — they are not meaningful energy-balance checks.
    """
    year_flows = flows[
        (flows["year"] == year)
        & (flows["variable"].str.upper().isin(["VAR_FIN", "VAR_FOUT"]))
    ].copy()
    # Drop region-level pseudo-process rows (process_code '-')
    if "process_code" in year_flows.columns:
        year_flows = year_flows[year_flows["process_code"].astype(str) != "-"]

    if pj_commodity_codes:
        year_flows = year_flows[
            year_flows["commodity_code"].astype(str).isin(pj_commodity_codes)
        ]

    if year_flows.empty:
        return pd.DataFrame(
            columns=[
                "commodity_code",
                "fout",
                "fin",
                "net_flows",
                "comnet",
                "residual",
                "ok",
            ]
        )

    fout = (
        year_flows[year_flows["variable"].str.upper() == "VAR_FOUT"]
        .groupby("commodity_code")["value"]
        .sum()
    )
    fin = (
        year_flows[year_flows["variable"].str.upper() == "VAR_FIN"]
        .groupby("commodity_code")["value"]
        .sum()
    )
    net = fout.subtract(fin, fill_value=0.0)

    cn = comnet[comnet["year"] == year] if not comnet.empty else pd.DataFrame()
    if not cn.empty:
        cn_s = cn.groupby("commodity_code")["value"].sum()
    else:
        cn_s = pd.Series(dtype=float)

    # GDX2VEDA reports VAR_Comnet for only a subset of commodities (91 of ~180 PJ
    # carriers in the reference scenario — not even ELCHIG or GASNAT). A commodity
    # absent from the table has no oracle; treating its missing value as 0 turned
    # every `.DEM.` service commodity (TAIF, RLIG, RCOK, …) into a fake residual.
    comnet_codes = set(cn_s.index)
    if require_flow_activity:
        codes = sorted(set(net.index) & comnet_codes)
    else:
        codes = sorted((set(net.index) & comnet_codes) | comnet_codes)

    rows = []
    for code in codes:
        n = float(net.get(code, 0.0))
        c = float(cn_s.get(code, 0.0))
        fo = float(fout.get(code, 0.0))
        fi = float(fin.get(code, 0.0))
        if require_flow_activity and (fo + fi) <= 0:
            continue
        residual = n - c
        rows.append(
            {
                "commodity_code": code,
                "fout": fo,
                "fin": fi,
                "net_flows": n,
                "comnet": c,
                "residual": residual,
                "ok": abs(residual) <= tolerance_pj,
            }
        )
    return pd.DataFrame(rows)


def commodity_node_residuals(
    netted_flows: pd.DataFrame,
    *,
    tolerance_pj: float = 1e-3,
) -> pd.DataFrame:
    """
    After netting, for each commodity node: inflow (from processes via FOut)
    vs outflow (to processes via FIn). Residuals are expected near zero for
    pure intermediate carriers; demand sinks / primary sources may residual.
    """
    if netted_flows.empty:
        return pd.DataFrame(
            columns=["commodity_code", "inflow", "outflow", "residual", "ok"]
        )

    links = sankey_links_from_flows(netted_flows)
    # Reconstruct commodity balance from FIN/FOUT on the netted frame
    fout = (
        netted_flows[netted_flows["variable"].str.upper() == "VAR_FOUT"]
        .groupby("commodity_code")["value"]
        .sum()
    )
    fin = (
        netted_flows[netted_flows["variable"].str.upper() == "VAR_FIN"]
        .groupby("commodity_code")["value"]
        .sum()
    )
    codes = sorted(set(fout.index) | set(fin.index))
    rows = []
    for code in codes:
        inflow = float(fout.get(code, 0.0))  # produced into commodity
        outflow = float(fin.get(code, 0.0))  # consumed from commodity
        residual = inflow - outflow
        rows.append(
            {
                "commodity_code": code,
                "inflow": inflow,
                "outflow": outflow,
                "residual": residual,
                "ok": abs(residual) <= tolerance_pj,
            }
        )
    return pd.DataFrame(rows)


def find_loop_components(netted_flows: pd.DataFrame) -> list[list[str]]:
    """
    Build directed Sankey graph and return strongly connected components
    with more than one node (surviving loopflows after netting).
    """
    if netted_flows.empty:
        return []

    links = sankey_links_from_flows(netted_flows)
    if links.empty:
        return []

    graph: dict[str, set[str]] = defaultdict(set)
    nodes: set[str] = set()
    for _, row in links.iterrows():
        s, t = str(row["source"]), str(row["target"])
        graph[s].add(t)
        nodes.add(s)
        nodes.add(t)

    # Tarjan SCC
    index = 0
    stack: list[str] = []
    on_stack: set[str] = set()
    indices: dict[str, int] = {}
    lowlink: dict[str, int] = {}
    sccs: list[list[str]] = []

    def strongconnect(v: str) -> None:
        nonlocal index
        indices[v] = index
        lowlink[v] = index
        index += 1
        stack.append(v)
        on_stack.add(v)
        for w in graph.get(v, ()):
            if w not in indices:
                strongconnect(w)
                lowlink[v] = min(lowlink[v], lowlink[w])
            elif w in on_stack:
                lowlink[v] = min(lowlink[v], indices[w])
        if lowlink[v] == indices[v]:
            comp: list[str] = []
            while True:
                w = stack.pop()
                on_stack.discard(w)
                comp.append(w)
                if w == v:
                    break
            if len(comp) > 1:
                sccs.append(sorted(comp))

    for n in sorted(nodes):
        if n not in indices:
            strongconnect(n)
    return sccs


def flow_key(row: pd.Series) -> tuple:
    return (
        int(row["year"]),
        str(row["process_code"]),
        str(row["commodity_code"]),
        str(row["variable"]).upper(),
    )


def double_count_matrix(
    matched: dict[str, set[tuple]],
    *,
    allowed: dict[str, frozenset[str]] | None = None,
) -> pd.DataFrame:
    """
    Pairwise overlap of matched flow keys between categories.

    Returns rows for overlaps that are NOT covered by the parent/child allowlist.
    """
    allowed = allowed or ALLOWED_OVERLAPS
    cats = sorted(matched.keys())
    bad_rows = []
    for i, a in enumerate(cats):
        for b in cats[i + 1 :]:
            overlap = matched[a] & matched[b]
            if not overlap:
                continue
            # Allowed if one is parent of the other
            ok = False
            if a in allowed and b in allowed[a]:
                ok = True
            if b in allowed and a in allowed[b]:
                ok = True
            if ok:
                continue
            bad_rows.append(
                {
                    "category_a": a,
                    "category_b": b,
                    "n_overlap": len(overlap),
                    "example_key": str(next(iter(overlap))),
                }
            )
    return pd.DataFrame(bad_rows)


def parent_child_sum_checks(
    category_pj: dict[str, float],
    *,
    tolerance_pj: float = 0.05,
) -> pd.DataFrame:
    """Check documented parent ≈ sum(children) identities."""
    checks = [
        (
            "BEWAL residential urban decentral heat",
            [
                "residential urban decentral gas boiler",
                "residential urban decentral coal boiler",
                "residential urban decentral electric heater",
                "residential urban decentral heat pump",
                "residential urban decentral geothermal",
                "residential urban decentral biomass boiler",
                "residential urban decentral solar thermal",
                "residential urban decentral oil boiler",
            ],
        ),
        (
            "BEWAL residential rural heat",
            [
                "residential rural gas boiler",
                "residential rural coal boiler",
                "residential rural electric heater",
                "residential rural heat pump",
                "residential rural geothermal",
                "residential rural biomass boiler",
                "residential rural solar thermal",
                "residential rural oil boiler",
            ],
        ),
        (
            "BEWAL services urban decentral heat",
            [
                "services gas boiler",
                "services biomass boiler",
                "services heat pump",
                "services oil boiler",
                "services geothermal",
                "services electric heater",
                "services solar thermal",
                "services CHP heat",
            ],
        ),
    ]
    rows = []
    for parent, children in checks:
        parent_v = float(category_pj.get(parent, 0.0))
        child_sum = sum(float(category_pj.get(c, 0.0)) for c in children)
        residual = parent_v - child_sum
        rows.append(
            {
                "parent": parent,
                "parent_pj": parent_v,
                "children_sum_pj": child_sum,
                "residual_pj": residual,
                "ok": abs(residual) <= tolerance_pj,
            }
        )

    # Agriculture: elec + heat + machinery ≤ total
    tot = float(category_pj.get("total agriculture", 0.0))
    parts = sum(
        float(category_pj.get(c, 0.0))
        for c in (
            "total agriculture electricity",
            "total agriculture heat",
            "total agriculture machinery",
        )
    )
    rows.append(
        {
            "parent": "total agriculture (≥ parts)",
            "parent_pj": tot,
            "children_sum_pj": parts,
            "residual_pj": tot - parts,
            "ok": parts <= tot + tolerance_pj,
        }
    )
    return pd.DataFrame(rows)


def classify_io_ratio(
    inflow: float,
    outflow: float,
    *,
    unmapped_in: float = 0.0,
    activity_unit: str = "",
    zero_tol: float = 1e-9,
) -> str:
    """
    One-word interpretation of an ΣFOut/ΣFIn ratio (see ``process_io_ratios``).

    The label is a *reading aid*, not a verdict: ratios > 1 are legitimate for
    heat pumps (COP) and for TIMES service accounting (lighting, vkm), and a
    "no energy input" process is legitimate for imports / mining / renewables.
    """
    has_in = inflow > zero_tol
    has_out = outflow > zero_tol
    if not has_in and not has_out:
        return "empty"
    if not has_in:
        # No energy FIn at all: primary supply, or an input we failed to map.
        return (
            "no energy input (unmapped FIn)"
            if unmapped_in > zero_tol
            else "source (no energy input)"
        )
    if not has_out:
        return "final demand / sink"
    ratio = outflow / inflow
    if unmapped_in > 0.05 * inflow:
        return "ratio distorted (unmapped FIn)"
    if activity_unit and activity_unit.upper() not in ("PJ", ""):
        return "non-PJ activity output"
    if ratio > 3.0:
        # Output dwarfs input: primary supply (imports, mining, renewables) or an
        # input the mapping never captured — not a plausible conversion or COP.
        return "mostly source (input ≪ output)"
    if ratio > 1.05:
        return "COP / service accounting"
    if ratio < 0.5:
        return "high losses (check outputs)"
    return "conversion"


def process_io_ratios(
    flows: pd.DataFrame,
    *,
    by: str = "process_code",
    carrier_mask: pd.Series | None = None,
    activity_units: dict[str, str] | None = None,
    label_cols: Iterable[str] = (),
) -> pd.DataFrame:
    """
    ΣVAR_FOut / ΣVAR_FIn per process — efficiencies, COPs, and energy balances.

    ``by`` is the grouping column: ``process_code`` for TIMES resolution, or an
    aggregated label column (e.g. ``process_node`` after :func:`aggregate_flows`)
    for the Sankey-node view. The ratio answers three questions at once:

    - **efficiency** — a boiler / power plant should sit at 0.3–1.0;
    - **COP** — heat pumps and TIMES service accounting sit above 1;
    - **energy balance** — ``balance = outflow − inflow`` should be ≤ 0 for any
      real conversion, so a positive balance means energy appears out of nowhere.

    ``carrier_mask`` marks the rows that pass the Sankey energy-carrier filter
    (:func:`times_pypsa.qa.filter_energy_carrier_flows`). Rows outside it are
    **not** counted in inflow/outflow but are reported as ``unmapped_in`` /
    ``unmapped_out``, because a missing commodity mapping is the single most
    common reason a ratio looks impossible.

    Returns one row per group with inflow / outflow / ratio / balance, the
    exported (soft-linked) share, distinct commodity counts, and a ``reading``
    column from :func:`classify_io_ratio`.
    """
    empty_cols = [
        by,
        "description",
        "sector",
        "activity_unit",
        "inflow",
        "outflow",
        "ratio",
        "balance",
        "exported_in",
        "exported_out",
        "unmapped_in",
        "unmapped_out",
        "n_commodities_in",
        "n_commodities_out",
        "reading",
    ]
    if flows.empty or by not in flows.columns:
        return pd.DataFrame(columns=empty_cols)

    df = flows[flows["variable"].astype(str).str.upper().isin(("VAR_FIN", "VAR_FOUT"))]
    if df.empty:
        return pd.DataFrame(columns=empty_cols)

    key = df[by].astype(str)
    is_out = df["variable"].astype(str).str.upper().eq("VAR_FOUT")
    if carrier_mask is None:
        in_scope = pd.Series(True, index=df.index)
    else:
        in_scope = carrier_mask.reindex(df.index).fillna(False).astype(bool)
    exported = (
        df["exported"].reindex(df.index).fillna(False).astype(bool)
        if "exported" in df.columns
        else pd.Series(False, index=df.index)
    )

    def _sum(mask: pd.Series) -> pd.Series:
        """Group-sum ``value`` over ``mask``, restricted to energy-carrier rows."""
        sel = mask & in_scope
        return df.loc[sel, "value"].groupby(key[sel], sort=False).sum()

    inflow = _sum(~is_out)
    outflow = _sum(is_out)
    exported_in = _sum(~is_out & exported)
    exported_out = _sum(is_out & exported)
    unmapped_in = df.loc[~is_out & ~in_scope, "value"].groupby(
        key[~is_out & ~in_scope], sort=False
    ).sum()
    unmapped_out = df.loc[is_out & ~in_scope, "value"].groupby(
        key[is_out & ~in_scope], sort=False
    ).sum()

    com_col = "commodity_code" if "commodity_code" in df.columns else by
    n_in = df.loc[~is_out & in_scope, com_col].groupby(
        key[~is_out & in_scope], sort=False
    ).nunique()
    n_out = df.loc[is_out & in_scope, com_col].groupby(
        key[is_out & in_scope], sort=False
    ).nunique()

    out = pd.DataFrame(index=pd.Index(sorted(set(key)), name=by))
    for name, series in (
        ("inflow", inflow),
        ("outflow", outflow),
        ("exported_in", exported_in),
        ("exported_out", exported_out),
        ("unmapped_in", unmapped_in),
        ("unmapped_out", unmapped_out),
    ):
        out[name] = series.reindex(out.index).fillna(0.0).astype(float)
    for name, series in (("n_commodities_in", n_in), ("n_commodities_out", n_out)):
        out[name] = series.reindex(out.index).fillna(0).astype(int)

    # First non-empty descriptive value per group (cheap, vectorized).
    for col, target in (("process", "description"), ("sector", "sector")):
        if col in df.columns:
            text = df[col].astype(str).str.strip().replace({"nan": ""})
            first = text[text.ne("")].groupby(key[text.ne("")], sort=False).first()
            out[target] = first.reindex(out.index).fillna("")
        else:
            out[target] = ""

    units = activity_units or {}
    out["activity_unit"] = [str(units.get(code, "")) for code in out.index]

    out["ratio"] = (out["outflow"] / out["inflow"]).where(out["inflow"] > 1e-9)
    # Snap summation noise to zero so a 1:1 pass-through reads as 0, not -7.9e-15.
    balance = out["outflow"] - out["inflow"]
    scale = out[["inflow", "outflow"]].max(axis=1)
    out["balance"] = balance.where(balance.abs() > 1e-9 * scale.clip(lower=1.0), 0.0)
    out["reading"] = [
        classify_io_ratio(
            row.inflow,
            row.outflow,
            unmapped_in=row.unmapped_in,
            activity_unit=row.activity_unit,
        )
        for row in out.itertuples()
    ]

    out = out.reset_index()
    for col in label_cols:
        if col in df.columns and col not in out.columns:
            first = df[col].astype(str).groupby(key, sort=False).first()
            out[col] = out[by].map(first).fillna("")
    out["throughput"] = out[["inflow", "outflow"]].max(axis=1)
    return out.sort_values("throughput", ascending=False).drop(columns=["throughput"])


#: Sectors whose *service output* pypsa-wal imports as a demand. A useful-heat or
#: service commodity produced here and matched by no rule is energy PyPSA never sees.
DEMAND_SECTORS = frozenset({"RSD", "COM", "AGR"})

#: PyPSA carriers the extraction rules claim to transfer as a **service output**
#: (``measure_at = service_output``) — today that is ``Heat`` and nothing else.
#:
#: Scoping the service-output check to these is what makes it a *closure* test
#: rather than noise. A demand sector also produces onto fuel commodities
#: (``RSDELC``, ``RSDGMX``, lighting, cooling …) that the soft-link deliberately
#: measures on the ``VAR_FIn`` side or not at all; counting those as "gap" buries
#: the real signal under ~280 PJ of by-design exclusions — the same mistake that
#: made the existing ``VAR_FIn`` coverage-gap table unreadable.
#:
#: Self-configuring: add a ``service_output`` rule on a new carrier and it is
#: covered automatically.
SERVICE_OUTPUT_CARRIERS = frozenset(
    meta.carrier
    for meta in _RULE_METADATA.values()
    if meta.measure_at == "service_output" and meta.carrier
)


def unmapped_process_report(
    flows: pd.DataFrame,
    mapped_processes: Iterable[str] | None = None,
    *,
    min_pj: float = 0.0,
) -> pd.DataFrame:
    """
    Energy-carrier flows whose process carries **no** ``Aggregation Level 2`` label.

    This is the root-cause check for the whole extraction: every rule in
    ``extraction_rules.csv`` filters on ``process_agg`` (= ``Aggregation Level 2``),
    so a process with a blank label can never be matched by any rule, in any
    sector, on either the ``VAR_FIn`` or the ``VAR_FOut`` side. Its energy is not
    "excluded" — it is invisible.

    Every other coverage check in this module is *relative to the rules*
    (:func:`parent_child_sum_checks` compares rule totals to rule totals;
    ``export_reconciliation`` compares tagged to coloured). They all pass while a
    process is missing from ``mapping_processes.csv``, because it contributes zero
    to both sides. This check is the only one that compares the rules to the
    ``.vd``.

    It found the 2026 Walloon heat leak: 16 building heating processes had no
    mapping row, so 2.2–7.7 % of TIMES appliance heat never reached PyPSA.

    ``mapped_processes`` (the ``Process`` column of ``mapping_processes.csv``)
    splits the cause into ``missing_row`` (the mapping file lags the ``.vd``) and
    ``blank_label`` (row present, label empty). Omit it and every row is reported
    as ``blank_label``.

    Returns one row per process, largest gross throughput first. **Empty is the
    expected result.**
    """
    cols = [
        "process_code",
        "process",
        "sector",
        "reason",
        "fin_pj",
        "fout_pj",
        "gross_pj",
        "commodities",
    ]
    if flows is None or flows.empty or "process_agg" not in flows.columns:
        return pd.DataFrame(columns=cols)

    label = flows["process_agg"].astype(str).str.strip()
    unlabelled = flows[label.isin(("", "nan", "None"))]
    if unlabelled.empty:
        return pd.DataFrame(columns=cols)

    known = (
        {str(p).strip() for p in mapped_processes}
        if mapped_processes is not None
        else None
    )
    var = unlabelled["variable"].astype(str).str.upper()
    rows = []
    for code, grp in unlabelled.groupby(unlabelled["process_code"].astype(str)):
        gvar = var.loc[grp.index]
        fin = float(grp.loc[gvar == "VAR_FIN", "value"].sum())
        fout = float(grp.loc[gvar == "VAR_FOUT", "value"].sum())
        if fin + fout <= min_pj:
            continue
        if known is None:
            reason = "blank_label"
        else:
            reason = "blank_label" if code.strip() in known else "missing_row"
        name = str(grp["process"].iloc[0]) if "process" in grp.columns else code
        sector = ""
        if "commodity_sector" in grp.columns:
            sectors = sorted(
                {s for s in grp["commodity_sector"].astype(str) if s and s != "nan"}
            )
            sector = "|".join(sectors)
        commodities = "|".join(
            sorted({str(c) for c in grp["commodity_code"].astype(str)})[:8]
        )
        rows.append(
            {
                "process_code": code,
                "process": name,
                "sector": sector,
                "reason": reason,
                "fin_pj": fin,
                "fout_pj": fout,
                "gross_pj": fin + fout,
                "commodities": commodities,
            }
        )
    if not rows:
        return pd.DataFrame(columns=cols)
    return (
        pd.DataFrame(rows, columns=cols)
        .sort_values("gross_pj", ascending=False)
        .reset_index(drop=True)
    )


def service_output_coverage(
    tagged: pd.DataFrame,
    *,
    demand_sectors: frozenset[str] = DEMAND_SECTORS,
    service_carriers: frozenset[str] = SERVICE_OUTPUT_CARRIERS,
) -> pd.DataFrame:
    """
    Closure of demand-sector **service output** (``VAR_FOut``): produced vs exported.

    The pre-existing coverage-gap check scans ``VAR_FIn`` only, so it is blind by
    construction to the rules that measure a *service output* — which is every
    building-heat rule (``measure_at = service_output``). This is the missing
    mirror: for each PyPSA carrier produced onto a demand-sector commodity, how
    much of it any rule matched.

    Keyed on ``commodity_sector`` (from ``mapping_commodities.csv``), **not** on the
    process's sector: a process absent from ``mapping_processes.csv`` has no sector
    at all, so a process-side filter silently drops exactly the rows that matter.

    A non-zero ``gap_pj`` on a carrier pypsa-wal consumes (``Heat`` above all) means
    TIMES produced service energy that the soft-link does not transfer.
    """
    cols = [
        "commodity_sector",
        "pypsa_carrier",
        "produced_pj",
        "exported_pj",
        "gap_pj",
        "exported_share",
        "n_unexported_rows",
    ]
    required = {"variable", "pypsa_carrier", "commodity_sector", "exported", "value"}
    if tagged is None or tagged.empty or not required.issubset(tagged.columns):
        return pd.DataFrame(columns=cols)

    fout = tagged[tagged["variable"].astype(str).str.upper() == "VAR_FOUT"].copy()
    fout["commodity_sector"] = fout["commodity_sector"].astype(str).str.strip()
    fout["pypsa_carrier"] = fout["pypsa_carrier"].astype(str).str.strip()
    fout = fout[
        fout["commodity_sector"].isin(demand_sectors)
        & fout["pypsa_carrier"].isin(service_carriers)
    ]
    if fout.empty:
        return pd.DataFrame(columns=cols)

    exp = fout["exported"].fillna(False).astype(bool)
    rows = []
    for (sector, carrier), grp in fout.groupby(
        ["commodity_sector", "pypsa_carrier"], sort=False
    ):
        mask = exp.loc[grp.index]
        produced = float(grp["value"].sum())
        exported = float(grp.loc[mask, "value"].sum())
        rows.append(
            {
                "commodity_sector": sector,
                "pypsa_carrier": carrier,
                "produced_pj": produced,
                "exported_pj": exported,
                "gap_pj": produced - exported,
                "exported_share": (exported / produced) if produced > 1e-12 else float("nan"),
                "n_unexported_rows": int((~mask).sum()),
            }
        )
    return (
        pd.DataFrame(rows, columns=cols)
        .sort_values("gap_pj", ascending=False)
        .reset_index(drop=True)
    )


def service_output_gap(
    tagged: pd.DataFrame,
    *,
    demand_sectors: frozenset[str] = DEMAND_SECTORS,
    service_carriers: frozenset[str] = SERVICE_OUTPUT_CARRIERS,
) -> pd.DataFrame:
    """
    Per-process detail behind :func:`service_output_coverage`: who leaks, and how much.

    One row per (sector, carrier, ``process_agg``, process) that produced onto a
    demand-sector service commodity and was matched by no rule. A blank
    ``process_agg`` here points straight at :func:`unmapped_process_report`; a
    non-blank one means the label exists but no rule lists it (the
    ``CHSADUM-DEM``/``other demand`` case).
    """
    cols = [
        "commodity_sector",
        "pypsa_carrier",
        "process_agg",
        "process_code",
        "process",
        "value",
    ]
    required = {"variable", "pypsa_carrier", "commodity_sector", "exported", "value"}
    if tagged is None or tagged.empty or not required.issubset(tagged.columns):
        return pd.DataFrame(columns=cols)

    df = tagged[tagged["variable"].astype(str).str.upper() == "VAR_FOUT"].copy()
    df["commodity_sector"] = df["commodity_sector"].astype(str).str.strip()
    df["pypsa_carrier"] = df["pypsa_carrier"].astype(str).str.strip()
    df = df[
        df["commodity_sector"].isin(demand_sectors)
        & df["pypsa_carrier"].isin(service_carriers)
        & ~df["exported"].fillna(False).astype(bool)
    ]
    if df.empty:
        return pd.DataFrame(columns=cols)
    df["process_agg"] = df["process_agg"].astype(str).str.strip().replace(
        {"nan": "", "None": ""}
    )
    if "process" not in df.columns:
        df["process"] = df["process_code"]
    out = (
        df.groupby(
            ["commodity_sector", "pypsa_carrier", "process_agg", "process_code", "process"],
            dropna=False,
        )["value"]
        .sum()
        .reset_index()
    )
    return out.sort_values("value", ascending=False).reset_index(drop=True)


def empty_rule_report(
    category_pj: dict[str, float],
    all_categories: Iterable[str],
    *,
    known_zeros: frozenset[str] = KNOWN_ZERO_CATEGORIES,
) -> pd.DataFrame:
    """Flag categories with zero PJ that are not on the known-zero allowlist."""
    rows = []
    for cat in all_categories:
        pj = float(category_pj.get(cat, 0.0))
        is_zero = abs(pj) < 1e-12
        allowed = cat in known_zeros
        rows.append(
            {
                "category": cat,
                "pj": pj,
                "is_zero": is_zero,
                "known_zero": allowed,
                "ok": (not is_zero) or allowed,
            }
        )
    return pd.DataFrame(rows)
