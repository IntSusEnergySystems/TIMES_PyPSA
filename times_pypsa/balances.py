"""Energy-balance, loop, coverage, and double-count checks for TIMES QA."""

from __future__ import annotations

import logging
from collections import defaultdict
from typing import Iterable

import pandas as pd

from times_pypsa.aggregation import sankey_links_from_flows

logger = logging.getLogger(__name__)

# Categories known to be zero in current TIMES-WAL scenarios (SOFTLINKING Q3)
KNOWN_ZERO_CATEGORIES = frozenset(
    {
        "ammonia",
        "methanol",
        "total international navigation",
        "coal",  # scen_corrige_251129: no hard-coal / lignite industry FIN in soft-link years
    }
)

# Parent → children: overlaps allowed (child ⊂ parent)
ALLOWED_OVERLAPS: dict[str, frozenset[str]] = {
    "BEWAL residential urban decentral heat": frozenset(
        {
            "residential urban decentral gas boiler",
            "residential urban decentral coal boiler",
            "residential urban decentral electric heater",
            "residential urban decentral heat pump",
            "residential urban decentral geothermal",
            "residential urban decentral biomass boiler",
            "residential urban decentral solar thermal",
            "residential urban decentral oil boiler",
        }
    ),
    "BEWAL residential rural heat": frozenset(
        {
            "residential rural gas boiler",
            "residential rural coal boiler",
            "residential rural electric heater",
            "residential rural heat pump",
            "residential rural geothermal",
            "residential rural biomass boiler",
            "residential rural solar thermal",
            "residential rural oil boiler",
        }
    ),
    "BEWAL services urban decentral heat": frozenset(
        {
            "services gas boiler",
            "services biomass boiler",
            "services heat pump",
            "services oil boiler",
            "services geothermal",
            "services electric heater",
            "services solar thermal",
        }
    ),
    "total road": frozenset({"electricity road", "hydrogen road"}),
    "total rail": frozenset({"electricity rail"}),
    "total agriculture": frozenset(
        {
            "total agriculture electricity",
            "total agriculture heat",
            "total agriculture machinery",
        }
    ),
    "total electricity residential": frozenset(),  # no children in rules
}


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

    if require_flow_activity:
        codes = sorted(set(net.index))
    else:
        codes = sorted(set(net.index) | set(cn_s.index))

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
