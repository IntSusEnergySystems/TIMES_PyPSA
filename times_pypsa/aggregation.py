"""Aggregation levels for TIMES Sankey / QA views."""

from __future__ import annotations

import logging

import pandas as pd

from times_pypsa.pipeline import net_bidirectional_links

logger = logging.getLogger(__name__)

# Columns shared between mapping CSVs that must not be used as agg levels.
EXCLUDED_AGG_COLUMNS = frozenset(
    {
        "Process",
        "Technology (Process)",
        "TIMES commodity",
        "Description",
        "Unit",
        "Set",
        "Type",
        "Activity unit",
        "Capacity unit",
        "Primary commodity group",
        "Time slice level",
        "Vintage",
        "PyPSA technology",
        "Upstream commodity",
        "Upstream process",
        "Comment",
    }
)

LEVEL_ALIASES: dict[str, str] = {
    "L0": "Sector",
    "L1": "Aggregation Level 1",
    "mapping": "Aggregation Level 2",
}

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
}


def shared_aggregation_columns(
    processes_df: pd.DataFrame, commodities_df: pd.DataFrame
) -> list[str]:
    """Return column names present in BOTH dataframes usable as aggregation levels."""
    shared = set(processes_df.columns) & set(commodities_df.columns)
    return sorted(c for c in shared if c not in EXCLUDED_AGG_COLUMNS)


def proc_agg_col(level: str) -> str:
    return f"proc_agg__{level}"


def com_agg_col(level: str) -> str:
    return f"com_agg__{level}"


def _resolve_agg_level(level: str) -> str:
    if level == "L2":
        return "L2"
    return LEVEL_ALIASES.get(level, level)


def _available_agg_levels(flows: pd.DataFrame) -> list[str]:
    return sorted(
        col[len("proc_agg__") :]
        for col in flows.columns
        if col.startswith("proc_agg__")
    )


def _nonempty_labels(series: pd.Series) -> pd.Series:
    labels = series.fillna("").astype(str).str.strip()
    return labels.where(~labels.str.lower().eq("nan") & labels.ne(""), other="")


def _clean_text(value: object) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return ""
    return text


def identity_label(*candidates: object) -> str:
    """
    First non-empty candidate (description, code, …).

    Never returns ``Unknown`` — labels must come from TIMES names/codes.
    """
    for candidate in candidates:
        text = _clean_text(candidate)
        if text:
            return text
    return "Unspecified"


def infer_overview_commodity_label(
    *,
    description: str = "",
    code: str = "",
    carrier: str = "",
    cluster: str = "",
) -> str:
    """Heuristic carrier-family label used when ``sankey_overview`` is missing."""
    text = f"{carrier} {cluster} {description} {code}".lower()
    code_u = _clean_text(code).upper()
    code_l = code_u.lower()
    carrier_l = _clean_text(carrier).lower()

    if code_u == "TRAETH" or "ethanol" in text:
        return "Biomass & biofuels"
    if "batelc" in code_l or "battery" in text:
        return "Electricity"
    if any(k in text for k in ("hydrogen", "h2 for", "h2 ")) or code_u.startswith("H2"):
        return "Hydrogen"
    if code_u.endswith("SOL") or "solar" in carrier_l or "solar" in text:
        if code_u in {"RENSOL"} or "renewable: solar" in carrier_l or "electricity: solar" in carrier_l:
            return "Electricity"
        return "Heat"
    if code_u == "NUCRSV" or "uranium" in text:
        return "Electricity"
    if "electricity" in text or any(k in carrier_l for k in ("hydro", "nuclear", "wind")):
        return "Electricity"
    if any(
        k in text
        for k in (
            "natural gas",
            "network gas",
            "biogas",
            "gas for",
            "gas from",
            "supply: natural",
            "fuel tech – biogas",
            "fuel tech - biogas",
        )
    ) or ("GMX" in code_u) or (code_u.endswith("GAS") and "BIOGAS" not in code_u):
        return "Gas"
    if any(k in text for k in ("coal", "coke", "lignite", "hard coal")) or any(
        x in code_u for x in ("COA", "COK", "COL")
    ):
        return "Coal & solids"
    if any(k in text for k in ("heat", "chaleur", "geothermal", "district")):
        return "Heat"
    if any(
        k in text
        for k in (
            "biomass",
            "biofuel",
            "biodiesel",
            "bioethanol",
            "wood",
            "chips",
            "black liquor",
            "rape",
            "starch",
            "waste",
        )
    ):
        return "Biomass & biofuels"
    if any(
        k in text
        for k in (
            "diesel",
            "gasoline",
            "oil",
            "kerosene",
            "lpg",
            "liquified petroleum",
            "naphtha",
            "residual fuel",
            "petroleum",
            "aviation",
            "navigation",
        )
    ) or any(x in code_u for x in ("OIL", "DST", "GSL", "KER", "LPG", "HFO", "LFO", "NAP")):
        return "Oil products"
    # Prefer a concrete TIMES description/code over a generic bucket
    return identity_label(description, carrier, cluster, code)


def infer_overview_process_label(
    *,
    sector: str = "",
    process_type: str = "",
    description: str = "",
    agg_level_2: str = "",
    code: str = "",
) -> str:
    """Heuristic role label used when ``sankey_overview`` is missing on a process."""
    sector_u = _clean_text(sector).upper()
    ptype = _clean_text(process_type).upper()
    l2 = _clean_text(agg_level_2).lower()
    desc = _clean_text(description).lower()
    code_u = _clean_text(code)
    code_l = code_u.lower()
    blob = f"{l2} {desc} {code_l}"

    if code_u in {"", "-"}:
        return identity_label(description, "System aggregate")
    if sector_u == "IMP" or "import" in blob or "export" in blob:
        return "Imports & trade"
    if ptype == "CHP" or "district heating" in blob or "chp" in blob:
        return "CHP & district heat"
    if sector_u == "ELC":
        return "Fuel conversion" if "fuel tech" in blob else "Power plants"
    if sector_u == "SUP":
        return "Fuel supply"
    if sector_u == "IND":
        return "Fuel conversion" if "fuel tech" in blob else "Industry"
    if sector_u in {"RSD", "COM"}:
        return "Fuel conversion" if "fuel tech" in blob else "Buildings"
    if sector_u == "TRA":
        return "Fuel conversion" if "fuel tech" in blob else "Transport"
    if sector_u == "AGR":
        return "Fuel conversion" if "fuel tech" in blob else "Agriculture"

    # Code-prefix fallbacks when Sector is missing from dictionaries
    if code_u.startswith(("RW", "RH", "CW", "CH", "CC", "COM", "RSD")):
        return "Buildings"
    if code_u.startswith(("ETSTP", "ECP", "EPV", "EWIND", "ELC")) or "_TGV_" in code_u:
        return "Power plants"
    if code_u.startswith(("IMP", "EXP")):
        return "Imports & trade"
    if code_u.startswith(("TRA", "T")) and any(
        k in code_l for k in ("car", "truck", "bus", "rail", "tra")
    ):
        return "Transport"
    if code_u.startswith("IND") or code_u.startswith("I"):
        if any(k in code_l for k in ("ind", "steel", "cement", "chem")):
            return "Industry"

    return identity_label(description, agg_level_2, code)


def _sector_label(code: str) -> str:
    code = (code or "").strip().upper()
    if not code:
        return ""
    return SECTOR_LABELS.get(code, code)


def _mapping_process_label(row: pd.Series) -> str:
    """Process node label from mapping CSVs / TIMES description / code."""
    for key in ("process_agg", "agg_level_2"):
        val = _clean_text(row.get(key))
        if val:
            return val
    return identity_label(row.get("process"), row.get("process_code"))


def _mapping_commodity_label(row: pd.Series) -> str:
    """Commodity node label from mapping CSVs / TIMES description / code."""
    carrier = _clean_text(row.get("pypsa_carrier"))
    if carrier:
        return carrier
    return identity_label(row.get("commodity"), row.get("commodity_code"))


def _carrier_label(carrier: str, commodity: str, commodity_code: str) -> str:
    """Legacy helper: CSV carrier, else TIMES description/code."""
    return identity_label(carrier, commodity, commodity_code)


def _process_identity_series(df: pd.DataFrame) -> pd.Series:
    desc = df["process"] if "process" in df.columns else pd.Series("", index=df.index)
    code = df["process_code"] if "process_code" in df.columns else pd.Series("", index=df.index)
    return pd.Series(
        [identity_label(d, c) for d, c in zip(desc.tolist(), code.tolist())],
        index=df.index,
    )


def _commodity_identity_series(df: pd.DataFrame) -> pd.Series:
    desc = df["commodity"] if "commodity" in df.columns else pd.Series("", index=df.index)
    code = (
        df["commodity_code"] if "commodity_code" in df.columns else pd.Series("", index=df.index)
    )
    return pd.Series(
        [identity_label(d, c) for d, c in zip(desc.tolist(), code.tolist())],
        index=df.index,
    )


def _overview_process_series(df: pd.DataFrame) -> pd.Series:
    return pd.Series(
        [
            infer_overview_process_label(
                sector=r.get("sector", ""),
                process_type=r.get("process_type", ""),
                description=r.get("process", ""),
                agg_level_2=r.get("agg_level_2", r.get("process_agg", "")),
                code=r.get("process_code", ""),
            )
            for _, r in df.iterrows()
        ],
        index=df.index,
    )


def _overview_commodity_series(df: pd.DataFrame) -> pd.Series:
    return pd.Series(
        [
            infer_overview_commodity_label(
                description=r.get("commodity", ""),
                code=r.get("commodity_code", ""),
                carrier=r.get("pypsa_carrier", ""),
                cluster=r.get("com_agg__Aggregation Level 1", ""),
            )
            for _, r in df.iterrows()
        ],
        index=df.index,
    )


def _apply_legacy_mapping_labels(df: pd.DataFrame) -> None:
    df["process_node"] = df.apply(_mapping_process_label, axis=1)
    df["commodity_node"] = df.apply(_mapping_commodity_label, axis=1)


def _apply_column_agg_labels(df: pd.DataFrame, resolved: str) -> None:
    pcol = proc_agg_col(resolved)
    ccol = com_agg_col(resolved)
    proc_labels = _nonempty_labels(df[pcol])
    com_labels = _nonempty_labels(df[ccol])

    if resolved == "sankey_overview":
        proc_labels = proc_labels.where(proc_labels.ne(""), _overview_process_series(df))
        com_labels = com_labels.where(com_labels.ne(""), _overview_commodity_series(df))
    else:
        if resolved in {"Aggregation Level 1", "Aggregation Level 2", "custom"}:
            proc_fallback = df.apply(_mapping_process_label, axis=1)
        else:
            proc_fallback = _process_identity_series(df)
        if resolved in {"Aggregation Level 2", "custom"}:
            com_fallback = df.apply(_mapping_commodity_label, axis=1)
        else:
            com_fallback = _commodity_identity_series(df)
        proc_labels = proc_labels.where(proc_labels.ne(""), proc_fallback)
        com_labels = com_labels.where(com_labels.ne(""), com_fallback)

        # custom: still-empty process labels → description-based cluster heuristics
        if resolved == "custom":
            still = proc_labels.eq("") | proc_labels.str.lower().eq("unknown")
            if still.any():
                proc_labels = proc_labels.where(~still, _overview_process_series(df))

    # Final safety: never emit empty / Unknown
    still_p = proc_labels.eq("") | proc_labels.str.lower().eq("unknown")
    still_c = com_labels.eq("") | com_labels.str.lower().eq("unknown")
    if still_p.any():
        proc_labels = proc_labels.where(~still_p, _process_identity_series(df))
    if still_c.any():
        com_labels = com_labels.where(~still_c, _commodity_identity_series(df))

    df["process_node"] = proc_labels
    df["commodity_node"] = com_labels


def aggregate_flows(
    flows: pd.DataFrame,
    level: str = "L0",
    *,
    apply_netting: bool = True,
    drop_empty_labels: bool = True,
) -> pd.DataFrame:
    """
    Collapse process / commodity labels to an aggregation level.

    ``level`` is a CSV column name shared by process and commodity mappings,
    or a legacy alias:

    - ``mapping`` / ``Aggregation Level 2``: process_agg × pypsa_carrier (QA default)
    - ``L0`` / ``Sector``: coarse sector view
    - ``L1`` / ``Aggregation Level 1``: mid-level process grouping
    - ``L2``: individual process_code + commodity_code (no collapse)

    When enriched ``proc_agg__*`` / ``com_agg__*`` columns exist on ``flows``,
    labels are taken from those columns (vectorized). Otherwise ``mapping`` /
    ``Aggregation Level 2`` fall back to legacy ``process_agg`` / ``pypsa_carrier``
    columns.

    Returns a frame with columns suitable for Sankey:
        year, region, variable, process_code, process, commodity_code, commodity, value
    where process_code/commodity_code hold the aggregated node ids.
    """
    if flows.empty:
        return flows.copy()

    df = flows.copy()
    df = df[df["variable"].str.upper().isin(["VAR_FIN", "VAR_FOUT"])].copy()
    if df.empty:
        return df

    resolved = _resolve_agg_level(level)

    if resolved == "L2" or level == "L2":
        df["process_node"] = df["process_code"].astype(str)
        df["commodity_node"] = df["commodity_code"].astype(str)
    elif level == "L0":
        df["process_node"] = _nonempty_labels(df["sector"].map(_sector_label))
        df["process_node"] = df["process_node"].where(
            df["process_node"].ne(""), _process_identity_series(df)
        )
        if "commodity_sector" in df.columns:
            com_nodes = df.apply(
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
            com_nodes = df.apply(
                lambda r: _carrier_label(
                    r.get("pypsa_carrier", ""),
                    r.get("commodity", ""),
                    r.get("commodity_code", ""),
                ),
                axis=1,
            )
        df["commodity_node"] = _nonempty_labels(com_nodes)
        df["commodity_node"] = df["commodity_node"].where(
            df["commodity_node"].ne(""), _commodity_identity_series(df)
        )
    else:
        pcol = proc_agg_col(resolved)
        ccol = com_agg_col(resolved)
        has_proc = pcol in df.columns
        has_com = ccol in df.columns
        mapping_fallback = resolved == "Aggregation Level 2" or level == "mapping"

        if has_proc and has_com:
            _apply_column_agg_labels(df, resolved)
        elif mapping_fallback:
            _apply_legacy_mapping_labels(df)
        else:
            available = _available_agg_levels(df)
            raise ValueError(
                f"Aggregation level {level!r} (resolved {resolved!r}) is not available "
                f"on enriched flows (missing {pcol!r} or {ccol!r}). "
                f"Available levels: {available or ['L0', 'L1', 'L2', 'mapping']}"
            )

    if drop_empty_labels:
        df = df[
            (df["process_node"].astype(str).str.strip() != "")
            & (df["commodity_node"].astype(str).str.strip() != "")
        ]

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
    for extra in ("matched_categories", "exported"):
        if extra in out.columns:
            keep.append(extra)
    out = out[keep]

    if apply_netting:
        tags = None
        if "matched_categories" in df.columns or "exported" in df.columns:
            tags = df[
                ["year", "region", "process_node", "commodity_node", "variable"]
                + [c for c in ("matched_categories", "exported") if c in df.columns]
            ].copy()

        netted = net_bidirectional_links(out)
        if tags is not None and not netted.empty:
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
                    columns=[
                        c for c in ("process_node", "commodity_node") if c in netted.columns
                    ]
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
                    columns=[
                        c for c in ("process_node", "commodity_node") if c in netted.columns
                    ]
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


def merge_export_statuses(
    statuses: pd.Series,
    *,
    any_exported: bool = False,
) -> str:
    """
    Combine row-level export statuses when collapsing or aggregating Sankey links.

    Statuses: ``exported``, ``context``, ``mixed``, ``double_count``.

    Default (``any_exported=False``): ``exported`` + ``context`` → ``mixed``
    (strict; used when several physical rows share one bipartite link).

    With ``any_exported=True`` (commodity-hub collapse to process→process):
    ``exported`` + ``context`` → ``exported``. A balanced collapse is one Sankey
    flow; a single exported endpoint is enough to colour it blue for PyPSA.

    ``double_count`` = both FOut and FIn endpoints exported (soft-link overlap risk).
    ``mixed`` on either side still wins over blue/grey.
    """
    values = {
        str(v)
        for v in statuses.dropna()
        if str(v) in {"exported", "context", "mixed", "double_count"}
    }
    if "mixed" in values:
        return "mixed"
    if "double_count" in values:
        return "double_count"
    if "exported" in values and "context" in values:
        return "exported" if any_exported else "mixed"
    if "exported" in values:
        return "exported"
    return "context"


def collapse_pair_export_status(prod_status: str, cons_status: str) -> str:
    """
    Colour a collapsed producer→consumer link from the two endpoint statuses.

    - both ``exported`` → ``double_count`` (purple; FOut and FIn both soft-linked)
    - exactly one ``exported`` → ``exported`` (blue)
    - either ``mixed`` → ``mixed``
    - else ``context``
    """
    p = str(prod_status or "context")
    c = str(cons_status or "context")
    if p == "mixed" or c == "mixed":
        return "mixed"
    if p == "double_count" or c == "double_count":
        return "double_count"
    if p == "exported" and c == "exported":
        return "double_count"
    if p == "exported" or c == "exported":
        return "exported"
    return "context"


def format_export_via(attribute: str, process: str) -> str:
    """Human-readable export pathway for hover text, e.g. ``VAR_FIn of TRADST00``."""
    attr = str(attribute or "").strip()
    proc = str(process or "").strip()
    if not attr or not proc:
        return ""
    return f"{attr} of {proc}"


def collapse_export_detail(
    prod_status: str,
    cons_status: str,
    prod_name: str,
    cons_name: str,
) -> str:
    """Describe which endpoint(s) drive PyPSA export on a collapsed link."""
    parts: list[str] = []
    p = str(prod_status or "context")
    c = str(cons_status or "context")
    if p in {"exported", "mixed", "double_count"}:
        via = format_export_via("VAR_FOut", prod_name)
        if via:
            parts.append(via)
    if c in {"exported", "mixed", "double_count"}:
        via = format_export_via("VAR_FIn", cons_name)
        if via:
            parts.append(via)
    return " and ".join(parts)


def _join_export_details(series) -> str:
    """Merge export_detail strings from several collapsed contributions."""
    seen: set[str] = set()
    out: list[str] = []
    for item in series:
        text = str(item or "").strip()
        if not text:
            continue
        for piece in text.split("; "):
            piece = piece.strip()
            if piece and piece not in seen:
                seen.add(piece)
                out.append(piece)
    return "; ".join(out)


def _merge_export_statuses_any(statuses: pd.Series) -> str:
    """groupby-compatible wrapper: collapse links use any-exported colouring."""
    return merge_export_statuses(statuses, any_exported=True)


def build_sankey_label_map(
    flows: pd.DataFrame,
    level: str,
    *,
    apply_netting: bool = False,
) -> pd.DataFrame:
    """
    Map aggregated Sankey node labels back to original TIMES process/commodity codes.

    Returns columns:
        side, sankey_label, times_code, times_description, mapping_label, value
    where ``mapping_label`` is the raw CSV aggregation value before inference fallbacks.
    """
    if flows.empty:
        return pd.DataFrame(
            columns=[
                "side",
                "sankey_label",
                "times_code",
                "times_description",
                "mapping_label",
                "value",
            ]
        )

    resolved = _resolve_agg_level(level)
    df = flows.copy()
    df = df[df["variable"].str.upper().isin(["VAR_FIN", "VAR_FOUT"])].copy()
    if df.empty:
        return pd.DataFrame(
            columns=[
                "side",
                "sankey_label",
                "times_code",
                "times_description",
                "mapping_label",
                "value",
            ]
        )

    # Attach the same labels used by aggregate_flows without collapsing rows
    if resolved == "L2" or level == "L2":
        df["process_node"] = df["process_code"].astype(str)
        df["commodity_node"] = df["commodity_code"].astype(str)
        df["_proc_map"] = df["process_code"].astype(str)
        df["_com_map"] = df["commodity_code"].astype(str)
    elif level == "L0":
        df["process_node"] = _nonempty_labels(df["sector"].map(_sector_label))
        df["process_node"] = df["process_node"].where(
            df["process_node"].ne(""), _process_identity_series(df)
        )
        if "commodity_sector" in df.columns:
            com_nodes = df.apply(
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
            com_nodes = _commodity_identity_series(df)
        df["commodity_node"] = _nonempty_labels(com_nodes)
        df["commodity_node"] = df["commodity_node"].where(
            df["commodity_node"].ne(""), _commodity_identity_series(df)
        )
        df["_proc_map"] = df["sector"] if "sector" in df.columns else ""
        df["_com_map"] = (
            df["commodity_sector"] if "commodity_sector" in df.columns else ""
        )
    else:
        pcol = proc_agg_col(resolved)
        ccol = com_agg_col(resolved)
        if pcol in df.columns and ccol in df.columns:
            _apply_column_agg_labels(df, resolved)
            df["_proc_map"] = _nonempty_labels(df[pcol])
            df["_com_map"] = _nonempty_labels(df[ccol])
        elif resolved == "Aggregation Level 2" or level == "mapping":
            _apply_legacy_mapping_labels(df)
            df["_proc_map"] = df.apply(_mapping_process_label, axis=1)
            df["_com_map"] = df.apply(_mapping_commodity_label, axis=1)
        else:
            raise ValueError(f"Cannot build label map for level {level!r}")

    proc = (
        df.groupby(
            ["process_node", "process_code", "process", "_proc_map"], dropna=False
        )["value"]
        .sum()
        .reset_index()
        .rename(
            columns={
                "process_node": "sankey_label",
                "process_code": "times_code",
                "process": "times_description",
                "_proc_map": "mapping_label",
            }
        )
    )
    proc.insert(0, "side", "process")

    com = (
        df.groupby(
            ["commodity_node", "commodity_code", "commodity", "_com_map"], dropna=False
        )["value"]
        .sum()
        .reset_index()
        .rename(
            columns={
                "commodity_node": "sankey_label",
                "commodity_code": "times_code",
                "commodity": "times_description",
                "_com_map": "mapping_label",
            }
        )
    )
    com.insert(0, "side", "commodity")

    out = pd.concat([proc, com], ignore_index=True)
    out = out.sort_values(
        ["side", "sankey_label", "value"], ascending=[True, True, False]
    ).reset_index(drop=True)
    if apply_netting:
        # Map is pre-netting by design (member inventory); flag kept for API symmetry.
        pass
    return out


def sankey_links_from_flows(
    flows: pd.DataFrame,
    *,
    flow_threshold: float = 0.0,
) -> pd.DataFrame:
    """
    Build Sankey link table: source → target with value.

    VAR_FIn  : commodity → process
    VAR_FOut : process → commodity

    Adds ``source_kind`` / ``target_kind`` in {process, commodity} so renderers can
    colour process and commodity nodes differently (and keep same-name nodes distinct).
    Adds ``export_status`` in {exported, context, mixed} when export tags exist.
    """
    empty_cols = [
        "source",
        "target",
        "source_kind",
        "target_kind",
        "value",
        "export_status",
        "exported",
        "matched_categories",
        "commodity",
    ]
    if flows.empty:
        return pd.DataFrame(columns=empty_cols)

    df = flows.copy()
    var_u = df["variable"].str.upper()
    is_fin = var_u == "VAR_FIN"
    df["source"] = df["commodity_code"].where(is_fin, df["process_code"])
    df["target"] = df["process_code"].where(is_fin, df["commodity_code"])
    df["source_kind"] = pd.Series(
        ["commodity" if fin else "process" for fin in is_fin], index=df.index
    )
    df["target_kind"] = pd.Series(
        ["process" if fin else "commodity" for fin in is_fin], index=df.index
    )
    if "commodity" in df.columns:
        df["commodity"] = df["commodity"].fillna(df["commodity_code"]).astype(str)
    else:
        df["commodity"] = df["commodity_code"].astype(str)

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

    def _join_commodities(series):
        return "|".join(sorted({str(x) for x in series if str(x).strip()}))

    agg_spec["commodity"] = _join_commodities

    group_cols = ["source", "target", "source_kind", "target_kind"]
    links = df.groupby(group_cols, as_index=False).agg(agg_spec)

    if status_source == "exported":
        status = (
            df.groupby(group_cols)["exported"]
            .apply(classify_export_status)
            .reset_index(name="export_status")
        )
        links = links.merge(status, on=group_cols, how="left")
    elif status_source == "export_status":
        status = (
            df.groupby(group_cols)["export_status"]
            .apply(merge_export_statuses)
            .reset_index(name="export_status")
        )
        links = links.merge(status, on=group_cols, how="left")

    if "export_status" in links.columns:
        links["export_status"] = links["export_status"].fillna("context")
        links["exported"] = links["export_status"].eq("exported")

    if flow_threshold > 0:
        links = links[links["value"] > flow_threshold]
    return links


def commodity_fin_fout_balance(
    flows: pd.DataFrame,
    *,
    abs_tol: float = 1e-6,
) -> pd.DataFrame:
    """
    Compare Σ VAR_FOut vs Σ VAR_FIn for each commodity hub.

    Returns columns:
        commodity_code, fout, fin, residual, rel_err, balanced
    """
    empty = pd.DataFrame(
        columns=["commodity_code", "fout", "fin", "residual", "rel_err", "balanced"]
    )
    if flows.empty:
        return empty

    df = flows[flows["variable"].str.upper().isin(["VAR_FIN", "VAR_FOUT"])].copy()
    if df.empty:
        return empty

    fout = (
        df[df["variable"].str.upper() == "VAR_FOUT"]
        .groupby("commodity_code")["value"]
        .sum()
    )
    fin = (
        df[df["variable"].str.upper() == "VAR_FIN"]
        .groupby("commodity_code")["value"]
        .sum()
    )
    codes = sorted(set(fout.index) | set(fin.index))
    rows = []
    for code in codes:
        fo = float(fout.get(code, 0.0))
        fi = float(fin.get(code, 0.0))
        residual = fo - fi
        scale = max(abs(fo), abs(fi), abs_tol)
        rel_err = abs(residual) / scale
        rows.append(
            {
                "commodity_code": code,
                "fout": fo,
                "fin": fi,
                "residual": residual,
                "rel_err": rel_err,
                "balanced": abs(residual) <= abs_tol,
            }
        )
    return pd.DataFrame(rows)


class CommodityImbalanceError(ValueError):
    """Raised when a commodity hub has relative FIN/FOUT imbalance ≥ threshold."""

    def __init__(self, message: str, balance: pd.DataFrame):
        super().__init__(message)
        self.balance = balance


def _join_category_values(values) -> str:
    cats: set[str] = set()
    for item in values:
        if isinstance(item, list):
            cats.update(str(x) for x in item if x)
        elif isinstance(item, str) and item:
            cats.update(x for x in item.split("|") if x)
    return "|".join(sorted(cats))


def imbalance_process_label(
    commodity: str,
    *,
    fout: float,
    fin: float,
    rel_err: float,
    warn_rel: float = 0.10,
) -> str:
    """
    Artificial process-node name for *unexplained* imbalances.

    Examples:
        Unbalanced ≥10%: Oil products (FOut>FIn, 78.9%)
        Unbalanced <10%: Electricity (FOut>FIn, 3.6%)
    """
    direction = "FOut>FIn" if fout >= fin else "FIn>FOut"
    pct = 100.0 * float(rel_err)
    if rel_err >= warn_rel:
        return f"Unbalanced ≥{100.0 * warn_rel:.0f}%: {commodity} ({direction}, {pct:.1f}%)"
    return f"Unbalanced <{100.0 * warn_rel:.0f}%: {commodity} ({direction}, {pct:.1f}%)"


def is_pj_activity_unit(unit: str | None) -> bool:
    return str(unit or "").strip().upper() == "PJ"


def excluded_non_pj_consumers(
    reference_flows: pd.DataFrame,
    commodity_code: str,
    *,
    process_activity_units: dict[str, str],
    abs_tol: float = 1e-6,
) -> pd.DataFrame:
    """
    VAR_FIn consumers of ``commodity_code`` whose process Activity unit is not PJ.

    Only processes present in ``process_activity_units`` are considered. Aggregated
    Sankey labels (absent from that map) are ignored — otherwise every unknown code
    would look like a non-PJ exclusion.

    Returns columns: process_code, process, value, activity_unit.
    """
    empty = pd.DataFrame(
        columns=["process_code", "process", "value", "activity_unit"]
    )
    if reference_flows.empty or not process_activity_units:
        return empty
    df = reference_flows[
        (reference_flows["commodity_code"].astype(str) == str(commodity_code))
        & (reference_flows["variable"].str.upper() == "VAR_FIN")
    ].copy()
    if df.empty:
        return empty
    rows: list[dict] = []
    for proc, sub in df.groupby("process_code", sort=False):
        if str(proc) not in process_activity_units:
            continue
        unit = process_activity_units[str(proc)]
        if is_pj_activity_unit(unit):
            continue
        proc_label = (
            str(sub["process"].iloc[0])
            if "process" in sub.columns and len(sub)
            else str(proc)
        )
        value = float(sub["value"].sum())
        if value <= abs_tol:
            continue
        rows.append(
            {
                "process_code": str(proc),
                "process": proc_label if proc_label.strip() else str(proc),
                "value": value,
                "activity_unit": str(unit or "").strip() or "?",
            }
        )
    if not rows:
        return empty
    return pd.DataFrame(rows).sort_values("value", ascending=False).reset_index(drop=True)


def _reference_side_outside_view(
    reference_flows: pd.DataFrame,
    commodity_code: str,
    *,
    variable: str,
    present_processes: set[str],
    abs_tol: float = 1e-6,
) -> pd.DataFrame:
    """Reference FIN/FOUT rows for a commodity whose process is absent from the view."""
    empty = pd.DataFrame(columns=["process_code", "process", "value"])
    if reference_flows is None or reference_flows.empty:
        return empty
    df = reference_flows[
        (reference_flows["commodity_code"].astype(str) == str(commodity_code))
        & (reference_flows["variable"].str.upper() == variable.upper())
    ].copy()
    if df.empty:
        return empty
    rows: list[dict] = []
    for proc, sub in df.groupby("process_code", sort=False):
        if str(proc) in present_processes:
            continue
        value = float(sub["value"].sum())
        if value <= abs_tol:
            continue
        proc_label = (
            str(sub["process"].iloc[0])
            if "process" in sub.columns and len(sub)
            else str(proc)
        )
        rows.append(
            {
                "process_code": str(proc),
                "process": proc_label if proc_label.strip() else str(proc),
                "value": value,
            }
        )
    if not rows:
        return empty
    return pd.DataFrame(rows).sort_values("value", ascending=False).reset_index(drop=True)


def _residual_sink_plan(
    *,
    com_label: str,
    commodity_code: str,
    f_out: float,
    f_in: float,
    rel_err: float,
    producers: pd.DataFrame,
    consumers: pd.DataFrame,
    warn_rel: float,
    abs_tol: float,
    reference_flows: pd.DataFrame | None,
    process_activity_units: dict[str, str] | None,
) -> dict:
    """
    Decide how to label/log a commodity residual (FOut≠FIn).

    Returns dict with keys:
        expected (bool), class (str), label (str), tooltip (str),
        excluded (DataFrame|None) — non-PJ consumers when attributable.
    """
    residual = f_out - f_in
    slack = max(abs_tol, 1e-3 * max(abs(f_out), abs(f_in), 1.0))
    unexplained = {
        "expected": False,
        "class": "unexplained",
        "label": imbalance_process_label(
            com_label,
            fout=f_out,
            fin=f_in,
            rel_err=rel_err,
            warn_rel=warn_rel,
        ),
        "tooltip": (
            f"Unexplained commodity imbalance for '{com_label}': "
            f"ΣFOut={f_out:.4g} PJ, ΣFIn={f_in:.4g} PJ "
            f"(rel_err={100.0 * rel_err:.1f}%). "
            "Residual energy has no matching process on the opposite side."
        ),
        "excluded": None,
    }

    present_prod = (
        set(producers["process_code"].astype(str)) if not producers.empty else set()
    )
    present_cons = (
        set(consumers["process_code"].astype(str)) if not consumers.empty else set()
    )

    # FOut-only hub: typical TIMES DEM output (final demand), not an accounting error.
    # When ``reference_flows`` is provided (e.g. full system vs export neighbourhood),
    # require FIn≈0 there too — otherwise missing consumers are a view artifact.
    if residual > abs_tol and f_in <= abs_tol:
        ref_fin = 0.0
        if reference_flows is not None and not reference_flows.empty:
            ref = reference_flows[
                (reference_flows["commodity_code"].astype(str) == str(commodity_code))
                & (reference_flows["variable"].str.upper() == "VAR_FIN")
            ]
            ref_fin = float(ref["value"].sum()) if not ref.empty else 0.0
        if ref_fin <= abs_tol:
            if not producers.empty and len(producers) == 1:
                proc_name = str(producers.iloc[0]["process_code"])
            elif not producers.empty:
                # Dominant producer name when one process carries ≥90% of FOut.
                top = producers.sort_values("value", ascending=False).iloc[0]
                if float(top["value"]) >= 0.9 * f_out - abs_tol:
                    proc_name = str(top["process_code"])
                else:
                    proc_name = com_label
            else:
                proc_name = com_label
            return {
                "expected": True,
                "class": "final_demand",
                "label": proc_name,
                "tooltip": (
                    f"Final energy demand sink for commodity '{com_label}'. "
                    "No process consumes this commodity in the energy Sankey "
                    "(typical TIMES DEM / end-use output). "
                    f"Node '{proc_name}' is the demand-side process (or demand label); "
                    "magenta marks a non-energy accounting endpoint, not a conversion tech."
                ),
                "excluded": None,
            }

    # Missing FIN explained by non-PJ consumers dropped upstream of this frame.
    if (
        residual > abs_tol
        and reference_flows is not None
        and process_activity_units
    ):
        excluded = excluded_non_pj_consumers(
            reference_flows,
            str(commodity_code),
            process_activity_units=process_activity_units,
            abs_tol=abs_tol,
        )
        excl_sum = float(excluded["value"].sum()) if not excluded.empty else 0.0
        # Allow small numerical slack; residual should match excluded FIN mass.
        if excl_sum > abs_tol and abs(excl_sum - residual) <= slack:
            if len(excluded) == 1:
                proc_name = str(excluded.iloc[0]["process"])
                unit = str(excluded.iloc[0]["activity_unit"])
                tip = (
                    f"Energy delivered to process '{proc_name}' via commodity "
                    f"'{com_label}'. That process is excluded from the energy "
                    f"Sankey because its Activity unit is '{unit}' (not PJ). "
                    "Magenta marks the non-energy demand endpoint."
                )
            else:
                # Prefer a single shared process display name when all match.
                names = excluded["process"].astype(str)
                if names.nunique() == 1:
                    proc_name = str(names.iloc[0])
                else:
                    # Dominant consumer by energy; keep label short.
                    proc_name = str(
                        excluded.sort_values("value", ascending=False).iloc[0]["process"]
                    )
                units = sorted({str(u) for u in excluded["activity_unit"].unique()})
                tip = (
                    f"Energy delivered to {len(excluded)} non-PJ consumer process(es) "
                    f"via commodity '{com_label}' (Activity unit "
                    f"{', '.join(units)}). Those processes are excluded from the "
                    "energy Sankey; magenta marks the non-energy demand endpoint. "
                    f"Shown as '{proc_name}'."
                )
            return {
                "expected": True,
                "class": "excluded_consumer",
                "label": proc_name,
                "tooltip": tip,
                "excluded": excluded,
            }

    # Export-neighbourhood (or other) view truncation: the *full* reference hub is
    # approximately balanced, but this subset omits producers and/or consumers.
    if reference_flows is not None and abs(residual) > abs_tol:
        omitted_cons = _reference_side_outside_view(
            reference_flows,
            str(commodity_code),
            variable="VAR_FIN",
            present_processes=present_cons,
            abs_tol=abs_tol,
        )
        omitted_prod = _reference_side_outside_view(
            reference_flows,
            str(commodity_code),
            variable="VAR_FOUT",
            present_processes=present_prod,
            abs_tol=abs_tol,
        )
        omitted_fin = (
            float(omitted_cons["value"].sum()) if not omitted_cons.empty else 0.0
        )
        omitted_fout = (
            float(omitted_prod["value"].sum()) if not omitted_prod.empty else 0.0
        )
        if omitted_fin > abs_tol or omitted_fout > abs_tol:
            ref = reference_flows[
                reference_flows["commodity_code"].astype(str) == str(commodity_code)
            ]
            if not ref.empty:
                ref_fout = float(
                    ref.loc[ref["variable"].str.upper() == "VAR_FOUT", "value"].sum()
                )
                ref_fin = float(
                    ref.loc[ref["variable"].str.upper() == "VAR_FIN", "value"].sum()
                )
                ref_scale = max(abs(ref_fout), abs(ref_fin), abs_tol)
                ref_residual = ref_fout - ref_fin
                ref_rel = abs(ref_residual) / ref_scale
                expected_view_residual = ref_residual + omitted_fin - omitted_fout
                # Full system roughly balanced; view residual matches omitted sides.
                if (
                    ref_rel < warn_rel
                    and abs(expected_view_residual - residual) <= slack
                ):
                    if residual >= 0:
                        outside = (
                            omitted_cons if not omitted_cons.empty else omitted_prod
                        )
                        role = "consumer" if not omitted_cons.empty else "producer"
                    else:
                        outside = (
                            omitted_prod if not omitted_prod.empty else omitted_cons
                        )
                        role = "producer" if not omitted_prod.empty else "consumer"
                    proc_name = (
                        str(outside.iloc[0]["process"])
                        if not outside.empty
                        else com_label
                    )
                    tip = (
                        f"View truncation for commodity '{com_label}': "
                        f"the full system is nearly balanced "
                        f"(ΣFOut={ref_fout:.4g} / ΣFIn={ref_fin:.4g}), "
                        f"but this Sankey subset omits {len(omitted_cons)} consumer(s) "
                        f"({omitted_fin:.4g} PJ) and {len(omitted_prod)} producer(s) "
                        f"({omitted_fout:.4g} PJ) — typical of the export neighbourhood. "
                        f"Dominant omitted {role}: '{proc_name}'. "
                        "Not a TIMES accounting error."
                    )
                    return {
                        "expected": True,
                        "class": "neighbourhood_truncation",
                        "label": proc_name,
                        "tooltip": tip,
                        "excluded": None,
                    }

    return unexplained


def collapse_commodity_nodes(
    flows: pd.DataFrame,
    *,
    flow_threshold: float = 0.0,
    warn_rel: float = 0.10,
    abs_tol: float = 1e-6,
    raise_on_imbalance: bool = False,
    reference_flows: pd.DataFrame | None = None,
    process_activity_units: dict[str, str] | None = None,
) -> pd.DataFrame:
    """
    Remove commodity hubs so Sankey links run process → process.

    For each commodity ``C`` with producers (VAR_FOut) and consumers (VAR_FIn):

    - If ΣFOut ≈ ΣFIn: allocate flows proportionally and drop ``C``.
    - If the residual is an *expected* sink (final-demand FOut-only commodity, or
      FIN only on non-PJ processes visible in ``reference_flows``): route residual
      to a magenta node named after the demand process, log at INFO (not error).
    - If relative imbalance is unexplained and ``< warn_rel``: log a warning and
      route residual to an ``Unbalanced <…`` magenta node.
    - If unexplained and ``≥ warn_rel``: log an error (and optionally raise
      :class:`CommodityImbalanceError`), still collapsing matched energy.

    Pass ``reference_flows`` + ``process_activity_units`` (Process → Activity unit)
    when non-PJ consumers may have been filtered out of ``flows`` before collapse.

    Link colours still use ``export_status`` (exported / mixed / context).
    Collapsed links carry a ``commodity`` label for hover text; imbalance links
    also carry ``imbalance_class`` and ``imbalance_tooltip``.
    """
    empty_cols = [
        "source",
        "target",
        "source_kind",
        "target_kind",
        "value",
        "export_status",
        "exported",
        "matched_categories",
        "commodity",
        "export_detail",
        "imbalance_class",
        "imbalance_tooltip",
    ]
    if flows.empty:
        return pd.DataFrame(columns=empty_cols)

    balance = commodity_fin_fout_balance(flows, abs_tol=abs_tol)
    if balance.empty:
        return pd.DataFrame(columns=empty_cols)

    bal_by_code = balance.set_index("commodity_code")

    df = flows[flows["variable"].str.upper().isin(["VAR_FIN", "VAR_FOUT"])].copy()
    if "commodity" not in df.columns:
        df["commodity"] = df["commodity_code"].astype(str)
    else:
        df["commodity"] = df["commodity"].fillna(df["commodity_code"]).astype(str)
    if "matched_categories" not in df.columns:
        df["matched_categories"] = ""
    if "exported" in df.columns:
        df["exported"] = df["exported"].fillna(False).astype(bool)
    elif "export_status" in df.columns:
        df["exported"] = df["export_status"].map(
            lambda s: str(s) in {"exported", "mixed"}
        )
    else:
        df["exported"] = False
        df["export_status"] = "context"

    # Pre-compute sink plans so logging matches the labels used on links.
    sink_plans: dict[str, dict] = {}
    unexplained_err_codes: list[str] = []
    for code, row in bal_by_code.iterrows():
        if bool(row["balanced"]):
            continue
        code_s = str(code)
        grp = df[df["commodity_code"].astype(str) == code_s]
        com_label = (
            str(grp["commodity"].iloc[0]) if len(grp) else code_s
        )
        fout = grp[grp["variable"].str.upper() == "VAR_FOUT"]
        fin = grp[grp["variable"].str.upper() == "VAR_FIN"]
        producers = (
            fout.groupby("process_code", sort=False)["value"]
            .sum()
            .rename("value")
            .reset_index()
            if not fout.empty
            else pd.DataFrame(columns=["process_code", "value"])
        )
        consumers = (
            fin.groupby("process_code", sort=False)["value"]
            .sum()
            .rename("value")
            .reset_index()
            if not fin.empty
            else pd.DataFrame(columns=["process_code", "value"])
        )
        plan = _residual_sink_plan(
            com_label=com_label,
            commodity_code=code_s,
            f_out=float(row["fout"]),
            f_in=float(row["fin"]),
            rel_err=float(row["rel_err"]),
            producers=producers,
            consumers=consumers,
            warn_rel=warn_rel,
            abs_tol=abs_tol,
            reference_flows=reference_flows,
            process_activity_units=process_activity_units,
        )
        sink_plans[code_s] = plan
        if plan["expected"]:
            logger.info(
                "Commodity hub %r residual is expected (%s): FOut=%.4g, FIn=%.4g; "
                "routing to magenta sink %r.",
                code_s,
                plan["class"],
                float(row["fout"]),
                float(row["fin"]),
                plan["label"],
            )
        elif float(row["rel_err"]) < warn_rel:
            logger.warning(
                "Commodity hub %r is unbalanced (rel_err=%.1f%%, FOut=%.4g, FIn=%.4g); "
                "collapsing matched flows and routing residual to %r.",
                code_s,
                100.0 * float(row["rel_err"]),
                float(row["fout"]),
                float(row["fin"]),
                plan["label"],
            )
        else:
            unexplained_err_codes.append(code_s)
            logger.error(
                "Commodity hub %r is unbalanced by ≥%.0f%% (rel_err=%.1f%%, "
                "FOut=%.4g, FIn=%.4g); collapsing matched flows anyway and routing "
                "residual to %r.",
                code_s,
                100.0 * warn_rel,
                100.0 * float(row["rel_err"]),
                float(row["fout"]),
                float(row["fin"]),
                plan["label"],
            )

    if raise_on_imbalance and unexplained_err_codes:
        worst = (
            bal_by_code.loc[unexplained_err_codes]
            .sort_values("rel_err", ascending=False)
            .iloc[0]
        )
        raise CommodityImbalanceError(
            f"Commodity hub {worst.name!r} unbalanced by "
            f"{100.0 * float(worst['rel_err']):.1f}% "
            f"(threshold {100.0 * warn_rel:.0f}%).",
            balance=balance,
        )

    link_rows: list[dict] = []

    for code, grp in df.groupby("commodity_code", sort=False):
        com_label = str(grp["commodity"].iloc[0]) if len(grp) else str(code)
        fout = grp[grp["variable"].str.upper() == "VAR_FOUT"]
        fin = grp[grp["variable"].str.upper() == "VAR_FIN"]

        def _agg_process_side(side: pd.DataFrame) -> pd.DataFrame:
            if side.empty:
                return pd.DataFrame(
                    columns=[
                        "process_code",
                        "value",
                        "export_status",
                        "matched_categories",
                    ]
                )
            rows = []
            for proc, sub in side.groupby("process_code", sort=False):
                rows.append(
                    {
                        "process_code": proc,
                        "value": float(sub["value"].sum()),
                        "export_status": classify_export_status(sub["exported"]),
                        "matched_categories": _join_category_values(
                            sub["matched_categories"]
                        ),
                    }
                )
            return pd.DataFrame(rows)

        producers = _agg_process_side(fout)
        consumers = _agg_process_side(fin)

        f_out = float(producers["value"].sum()) if not producers.empty else 0.0
        f_in = float(consumers["value"].sum()) if not consumers.empty else 0.0

        if f_out <= abs_tol and f_in <= abs_tol:
            continue

        bal = bal_by_code.loc[code] if code in bal_by_code.index else None
        rel_err = float(bal["rel_err"]) if bal is not None else 0.0
        plan = sink_plans.get(str(code))
        if plan is None and abs(f_out - f_in) > abs_tol:
            plan = _residual_sink_plan(
                com_label=com_label,
                commodity_code=str(code),
                f_out=f_out,
                f_in=f_in,
                rel_err=rel_err,
                producers=producers,
                consumers=consumers,
                warn_rel=warn_rel,
                abs_tol=abs_tol,
                reference_flows=reference_flows,
                process_activity_units=process_activity_units,
            )
        artificial = str(plan["label"]) if plan else imbalance_process_label(
            com_label,
            fout=f_out,
            fin=f_in,
            rel_err=rel_err,
            warn_rel=warn_rel,
        )
        imb_class = str(plan["class"]) if plan else ""
        imb_tip = str(plan["tooltip"]) if plan else ""

        transferable = min(f_out, f_in)
        if transferable > abs_tol and f_out > abs_tol and f_in > abs_tol:
            for _, prod in producers.iterrows():
                transfer_p = float(prod["value"]) * (transferable / f_out)
                if transfer_p <= abs_tol:
                    continue
                for _, cons in consumers.iterrows():
                    share = float(cons["value"]) / f_in
                    value = transfer_p * share
                    if value <= abs_tol:
                        continue
                    if str(prod["process_code"]) == str(cons["process_code"]):
                        # Plotly Sankey cannot draw self-loops; drop internal recycle.
                        continue
                    status = collapse_pair_export_status(
                        str(prod["export_status"]), str(cons["export_status"])
                    )
                    detail = collapse_export_detail(
                        str(prod["export_status"]),
                        str(cons["export_status"]),
                        str(prod["process_code"]),
                        str(cons["process_code"]),
                    )
                    cats = _join_category_values(
                        [prod["matched_categories"], cons["matched_categories"]]
                    )
                    link_rows.append(
                        {
                            "source": str(prod["process_code"]),
                            "target": str(cons["process_code"]),
                            "source_kind": "process",
                            "target_kind": "process",
                            "value": value,
                            "export_status": status,
                            "exported": status in {"exported", "double_count"},
                            "matched_categories": cats,
                            "commodity": com_label,
                            "export_detail": detail,
                            "imbalance_class": "",
                            "imbalance_tooltip": "",
                        }
                    )

        # Residual → magenta sink (expected demand process or unexplained label).
        if f_out > f_in + abs_tol and not producers.empty:
            scale_resid = (f_out - transferable) / f_out
            for _, prod in producers.iterrows():
                value = float(prod["value"]) * scale_resid
                if value <= abs_tol:
                    continue
                pstat = str(prod["export_status"])
                link_rows.append(
                    {
                        "source": str(prod["process_code"]),
                        "target": artificial,
                        "source_kind": "process",
                        "target_kind": "imbalance",
                        "value": value,
                        "export_status": pstat,
                        "exported": pstat in {"exported", "double_count"},
                        "matched_categories": prod["matched_categories"],
                        "commodity": com_label,
                        "export_detail": (
                            format_export_via("VAR_FOut", str(prod["process_code"]))
                            if pstat in {"exported", "mixed", "double_count"}
                            else ""
                        ),
                        "imbalance_class": imb_class,
                        "imbalance_tooltip": imb_tip,
                    }
                )
        elif f_in > f_out + abs_tol and not consumers.empty:
            scale_resid = (f_in - transferable) / f_in
            for _, cons in consumers.iterrows():
                value = float(cons["value"]) * scale_resid
                if value <= abs_tol:
                    continue
                cstat = str(cons["export_status"])
                link_rows.append(
                    {
                        "source": artificial,
                        "target": str(cons["process_code"]),
                        "source_kind": "imbalance",
                        "target_kind": "process",
                        "value": value,
                        "export_status": cstat,
                        "exported": cstat in {"exported", "double_count"},
                        "matched_categories": cons["matched_categories"],
                        "commodity": com_label,
                        "export_detail": (
                            format_export_via("VAR_FIn", str(cons["process_code"]))
                            if cstat in {"exported", "mixed", "double_count"}
                            else ""
                        ),
                        "imbalance_class": imb_class,
                        "imbalance_tooltip": imb_tip,
                    }
                )

    if not link_rows:
        return pd.DataFrame(columns=empty_cols)

    links = pd.DataFrame(link_rows)
    group_cols = ["source", "target", "source_kind", "target_kind"]

    def _first_nonempty(series: pd.Series) -> str:
        for item in series:
            text = str(item or "").strip()
            if text:
                return text
        return ""

    agg = (
        links.groupby(group_cols, as_index=False)
        .agg(
            value=("value", "sum"),
            matched_categories=("matched_categories", _join_category_values),
            commodity=("commodity", lambda s: "|".join(sorted(set(map(str, s))))),
            export_status=("export_status", _merge_export_statuses_any),
            export_detail=("export_detail", _join_export_details),
            imbalance_class=("imbalance_class", _first_nonempty),
            imbalance_tooltip=("imbalance_tooltip", _first_nonempty),
        )
    )
    agg["exported"] = agg["export_status"].isin(["exported", "double_count"])
    if flow_threshold > 0:
        agg = agg[agg["value"] > flow_threshold]
    return agg.reset_index(drop=True)
