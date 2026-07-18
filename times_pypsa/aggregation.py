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
