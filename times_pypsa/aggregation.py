"""Aggregation levels for TIMES Sankey / QA views."""

from __future__ import annotations

import logging
from typing import Literal

import pandas as pd

from times_pypsa.pipeline import net_bidirectional_links

logger = logging.getLogger(__name__)

AggLevel = Literal["L0", "L1", "L2", "mapping"]

# Sector codes as stored in mapping_processes.csv (no invented display names)
SECTOR_LABELS = {
    "AGR": "AGR",
    "COM": "COM",
    "ELC": "ELC",
    "IMP": "IMP",
    "IND": "IND",
    "RSD": "RSD",
    "SUP": "SUP",
    "TRA": "TRA",
    "": "Unknown",
}


def _sector_label(code: str) -> str:
    code = (code or "").strip().upper()
    return SECTOR_LABELS.get(code, code or "Unknown")


def _mapping_process_label(row: pd.Series) -> str:
    """Process node label from mapping CSVs only (Aggregation Level 2 / process_agg)."""
    for key in ("process_agg", "agg_level_2"):
        val = str(row.get(key) or "").strip()
        if val and val.lower() != "nan":
            return val
    for key in ("process", "process_code"):
        val = str(row.get(key) or "").strip()
        if val and val.lower() != "nan":
            return val
    return "Unknown"


def _mapping_commodity_label(row: pd.Series) -> str:
    """Commodity node label from mapping CSVs only (PyPSA Energy Carrier)."""
    carrier = str(row.get("pypsa_carrier") or "").strip()
    if carrier and carrier.lower() != "nan":
        return carrier
    # Fall back to existing TIMES description / code — do not invent prefixes
    for key in ("commodity", "commodity_code"):
        val = str(row.get(key) or "").strip()
        if val and val.lower() != "nan":
            return val
    return "Unknown"


def _carrier_label(carrier: str, commodity: str, commodity_code: str) -> str:
    """Legacy helper: CSV carrier, else TIMES description/code (no invented prefix)."""
    c = (carrier or "").strip()
    if c and c.lower() not in {"nan", ""}:
        return c
    desc = (commodity or "").strip()
    if desc and desc.lower() not in {"nan", ""}:
        return desc
    return str(commodity_code or "Unknown")

def aggregate_flows(
    flows: pd.DataFrame,
    level: AggLevel = "L0",
    *,
    apply_netting: bool = True,
    drop_empty_labels: bool = True,
) -> pd.DataFrame:
    """
    Collapse process / commodity labels to an aggregation level.

    Levels
    ------
    mapping : process_agg (Aggregation Level 2) + pypsa_carrier — **preferred for QA**
              Labels come only from mapping CSVs (same formalism as extraction).
    L0 : process Sector code + commodity sector / carrier (coarse; optional)
    L1 : Aggregation Level 1 (fallback process_agg) + pypsa_carrier
    L2 : individual process_code + commodity_code (no collapse)

    Returns a frame with columns suitable for Sankey:
        year, region, variable, process_code, process, commodity_code, commodity, value
    where process_code/commodity_code hold the aggregated node ids.
    """
    if flows.empty:
        return flows.copy()

    df = flows.copy()
    # Restrict to energy flows
    df = df[df["variable"].str.upper().isin(["VAR_FIN", "VAR_FOUT"])].copy()
    if df.empty:
        return df

    if level == "mapping":
        df["process_node"] = df.apply(_mapping_process_label, axis=1)
        df["commodity_node"] = df.apply(_mapping_commodity_label, axis=1)
    elif level == "L0":
        df["process_node"] = df["sector"].map(_sector_label)
        if "commodity_sector" in df.columns:
            df["commodity_node"] = df.apply(
                lambda r: _sector_label(r["commodity_sector"])
                if str(r.get("commodity_sector") or "").strip()
                else _carrier_label(
                    r.get("pypsa_carrier", ""),
                    r.get("commodity", ""),
                    r.get("commodity_code", ""),
                ),
                axis=1,
            )
        else:
            df["commodity_node"] = df.apply(
                lambda r: _carrier_label(
                    r.get("pypsa_carrier", ""),
                    r.get("commodity", ""),
                    r.get("commodity_code", ""),
                ),
                axis=1,
            )
    elif level == "L1":
        def _l1_label(row) -> str:
            l1 = str(row.get("agg_level_1") or "").strip()
            if l1 and l1.lower() != "nan":
                return l1
            return _mapping_process_label(row)

        df["process_node"] = df.apply(_l1_label, axis=1)
        df["commodity_node"] = df.apply(_mapping_commodity_label, axis=1)
    else:  # L2
        df["process_node"] = df["process_code"].astype(str)
        df["commodity_node"] = df["commodity_code"].astype(str)

    if drop_empty_labels:
        df = df[
            (df["process_node"].astype(str).str.strip() != "")
            & (df["commodity_node"].astype(str).str.strip() != "")
        ]

    # Rewrite identity columns to aggregated nodes
    out = df.copy()
    out["process_code"] = out["process_node"]
    out["process"] = out["process_node"]
    out["commodity_code"] = out["commodity_node"]
    out["commodity"] = out["commodity_node"]

    keep = [
        "year",
        "region",
        "variable",
        "commodity_code",
        "commodity",
        "process_code",
        "process",
        "value",
    ]
    # Preserve match tags if present
    for extra in ("matched_categories", "exported"):
        if extra in out.columns:
            keep.append(extra)
    out = out[keep]

    if apply_netting:
        # Net within aggregated nodes (drops matched_categories — re-attach below)
        tags = None
        if "matched_categories" in df.columns or "exported" in df.columns:
            tags = df[
                ["year", "region", "process_node", "commodity_node", "variable"]
                + [
                    c
                    for c in ("matched_categories", "exported")
                    if c in df.columns
                ]
            ].copy()

        netted = net_bidirectional_links(out)
        if tags is not None and not netted.empty:
            # Best-effort: mark exported if any pre-net flow in that agg was exported
            tag_agg = tags.copy()
            if "exported" in tag_agg.columns:
                tag_agg["exported"] = tag_agg["exported"].fillna(False).astype(bool)
                exp = (
                    tag_agg.groupby(
                        ["year", "region", "process_node", "commodity_node"]
                    )["exported"]
                    .apply(classify_export_status)
                    .reset_index(name="export_status")
                )
                netted = netted.merge(
                    exp,
                    left_on=["year", "region", "process_code", "commodity_code"],
                    right_on=["year", "region", "process_node", "commodity_node"],
                    how="left",
                )
                netted["export_status"] = netted["export_status"].fillna("context")
                netted["exported"] = netted["export_status"].eq("exported")
                netted = netted.drop(
                    columns=[c for c in ("process_node", "commodity_node") if c in netted.columns]
                )
            if "matched_categories" in tag_agg.columns:
                def _join_cats(series):
                    cats: set[str] = set()
                    for item in series:
                        if isinstance(item, list):
                            cats.update(item)
                        elif isinstance(item, str) and item:
                            cats.update(x for x in item.split("|") if x)
                    return "|".join(sorted(cats))

                cats = (
                    tag_agg.groupby(
                        ["year", "region", "process_node", "commodity_node"]
                    )["matched_categories"]
                    .agg(_join_cats)
                    .reset_index()
                )
                netted = netted.merge(
                    cats,
                    left_on=["year", "region", "process_code", "commodity_code"],
                    right_on=["year", "region", "process_node", "commodity_node"],
                    how="left",
                )
                netted = netted.drop(
                    columns=[c for c in ("process_node", "commodity_node") if c in netted.columns]
                )
        out = netted

    logger.info(
        "Aggregated to %s: %d rows, %d process nodes, %d commodity nodes",
        level,
        len(out),
        out["process_code"].nunique() if not out.empty else 0,
        out["commodity_code"].nunique() if not out.empty else 0,
    )
    return out


def classify_export_status(exported: pd.Series) -> str:
    """
    Classify aggregated link export state.

    - exported: all contributing flows are exported to pypsa-wal
    - context: none are exported
    - mixed: exported and non-exported flows were merged (e.g. mapping aggregation)
    """
    exp = exported.fillna(False).astype(bool)
    has_exp = bool(exp.any())
    has_non = bool((~exp).any())
    if has_exp and has_non:
        return "mixed"
    if has_exp:
        return "exported"
    return "context"


def merge_export_statuses(statuses: pd.Series) -> str:
    """Combine row-level export statuses when collapsing Sankey links."""
    values = {str(v) for v in statuses.dropna() if str(v) in {"exported", "context", "mixed"}}
    if "mixed" in values:
        return "mixed"
    if "exported" in values and "context" in values:
        return "mixed"
    if "exported" in values:
        return "exported"
    return "context"


def sankey_links_from_flows(
    flows: pd.DataFrame,
    *,
    flow_threshold: float = 0.0,
) -> pd.DataFrame:
    """
    Build Sankey link table: source → target with value.

    VAR_FIn  : commodity → process
    VAR_FOut : process → commodity

    Adds ``export_status`` in {exported, context, mixed} when export tags exist.
    """
    empty_cols = [
        "source",
        "target",
        "value",
        "export_status",
        "exported",
        "matched_categories",
    ]
    if flows.empty:
        return pd.DataFrame(columns=empty_cols)

    df = flows.copy()
    df["source"] = df.apply(
        lambda r: r["commodity_code"]
        if str(r["variable"]).upper() == "VAR_FIN"
        else r["process_code"],
        axis=1,
    )
    df["target"] = df.apply(
        lambda r: r["process_code"]
        if str(r["variable"]).upper() == "VAR_FIN"
        else r["commodity_code"],
        axis=1,
    )

    agg_spec: dict = {"value": "sum"}
    status_source = None
    if "exported" in df.columns:
        df["exported"] = df["exported"].fillna(False).astype(bool)
        status_source = "exported"
    elif "export_status" in df.columns:
        status_source = "export_status"

    if "matched_categories" in df.columns:

        def _join_cats(series):
            cats: set[str] = set()
            for item in series:
                if isinstance(item, list):
                    cats.update(item)
                elif isinstance(item, str) and item:
                    cats.update(x for x in item.split("|") if x)
            return "|".join(sorted(cats))

        agg_spec["matched_categories"] = _join_cats

    links = df.groupby(["source", "target"], as_index=False).agg(agg_spec)

    if status_source == "exported":
        status = (
            df.groupby(["source", "target"])["exported"]
            .apply(classify_export_status)
            .reset_index(name="export_status")
        )
        links = links.merge(status, on=["source", "target"], how="left")
    elif status_source == "export_status":
        status = (
            df.groupby(["source", "target"])["export_status"]
            .apply(merge_export_statuses)
            .reset_index(name="export_status")
        )
        links = links.merge(status, on=["source", "target"], how="left")

    if "export_status" in links.columns:
        links["export_status"] = links["export_status"].fillna("context")
        links["exported"] = links["export_status"].eq("exported")

    if flow_threshold > 0:
        links = links[links["value"] > flow_threshold]
    return links
