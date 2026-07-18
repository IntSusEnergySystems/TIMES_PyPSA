"""Core TIMES → PyPSA extraction and Sankey pipeline (no Snakemake dependency)."""

from __future__ import annotations

import json
import logging
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from importlib.metadata import version
from pathlib import Path
from typing import Iterable, Literal

import pandas as pd
import plotly.graph_objects as go

logger = logging.getLogger(__name__)

PJ_TO_TWH = 0.277778
LIBRARY_VERSION = "0.1.0"
EmitMode = Literal["sankey", "demands", "all"]


def default_mappings_dir() -> Path:
    """Return the bundled default mappings directory."""
    return Path(__file__).resolve().parent / "mappings"


@dataclass
class PipelineConfig:
    """Configuration for TIMES extraction and Sankey generation."""

    start_year: int = 2021
    apply_netting: bool = True
    enable_process_clustering: bool = True
    group_commodities: bool = True
    process_cluster_column: str = "Aggregation Level 2"
    flow_threshold: float = 0.0


def parse_times_line(line: str) -> list[str]:
    """Parse a single line from a TIMES .vd file."""
    parts: list[str] = []
    current_part = ""
    in_quotes = False

    for char in line.strip():
        if char == '"':
            in_quotes = not in_quotes
        elif char == "," and not in_quotes:
            parts.append(current_part.strip('"'))
            current_part = ""
        else:
            current_part += char

    parts.append(current_part.strip('"'))
    return parts


def load_raw_records(vd_file_path: Path | str, start_year: int = 2021) -> pd.DataFrame:
    """
    Stream and collect raw records from a TIMES .vd file.

    Returns columns:
    [year, region, timeslice, variable, commodity_code, process_code, value]
    """
    vd_file_path = Path(vd_file_path)
    flows: list[dict] = []
    logger.info("Processing %s (all variables, all commodities)...", vd_file_path)

    line_count = 0
    kept_record_count = 0

    with vd_file_path.open() as f:
        for line in f:
            line_count += 1
            if not line.strip() or line.startswith("*"):
                continue

            try:
                parts = parse_times_line(line)
                if len(parts) < 9:
                    continue

                variable = parts[0]
                year = int(parts[3])
                if year < start_year:
                    continue

                value = float(parts[8])
                if value == 0:
                    continue

                kept_record_count += 1
                flows.append(
                    {
                        "year": year,
                        "region": parts[4],
                        "timeslice": parts[6],
                        "variable": variable,
                        "commodity_code": parts[1],
                        "process_code": parts[2],
                        "value": value,
                    }
                )
            except (ValueError, IndexError):
                continue

    logger.info("Total lines scanned: %d", line_count)
    logger.info(
        "Total records kept (year >= %d and non-zero): %d",
        start_year,
        kept_record_count,
    )
    return pd.DataFrame(flows)


def aggregate_to_annual(flows_df: pd.DataFrame) -> pd.DataFrame:
    """Aggregate timeslices to annual values."""
    if flows_df.empty:
        return flows_df

    return (
        flows_df.groupby(
            ["year", "region", "variable", "commodity_code", "process_code"],
            as_index=False,
        )["value"]
        .sum()
    )


def filter_for_sankey(
    annual_df: pd.DataFrame,
    year: int,
    mapping_df: pd.DataFrame | None = None,
    processes_df: pd.DataFrame | None = None,
) -> tuple[pd.DataFrame, set[str]]:
    """Filter annual flows for Sankey (VAR_FIn/VAR_FOut, PJ commodities/processes)."""
    if annual_df.empty:
        return annual_df, set()

    df = annual_df.copy()
    df = df[df["year"] == year]
    var_upper = df["variable"].str.upper()
    df = df[var_upper.isin(["VAR_FIN", "VAR_FOUT"])]

    energy_codes: set[str] = set()
    if (
        mapping_df is not None
        and not mapping_df.empty
        and "unit" in mapping_df.columns
        and "times" in mapping_df.columns
    ):
        energy_codes = set(
            mapping_df.loc[
                mapping_df["unit"].astype(str).str.strip().str.upper() == "PJ",
                "times",
            ]
            .astype(str)
            .str.strip()
            .unique()
        )

    if energy_codes and "commodity_code" in df.columns:
        original_rows = len(df)
        df = df[df["commodity_code"].astype(str).isin(energy_codes)]
        logger.info(
            "Kept %d of %d rows after commodity unit filtering (PJ only).",
            len(df),
            original_rows,
        )

    pj_process_codes: set[str] = set()
    if (
        processes_df is not None
        and not processes_df.empty
        and "Activity unit" in processes_df.columns
        and "Process" in processes_df.columns
    ):
        pj_process_codes = set(
            processes_df.loc[
                processes_df["Activity unit"].astype(str).str.strip().str.upper()
                == "PJ",
                "Process",
            ]
            .astype(str)
            .str.strip()
            .unique()
        )

    if pj_process_codes and "process_code" in df.columns:
        original_rows = len(df)
        df = df[df["process_code"].astype(str).isin(pj_process_codes)]
        logger.info(
            "Kept %d of %d rows after process unit filtering (PJ only).",
            len(df),
            original_rows,
        )

    return df, energy_codes


def analyze_process_connectivity(df: pd.DataFrame) -> tuple[set, set, set]:
    """Inspect process inflow/outflow connectivity."""
    if df.empty:
        logger.info("Filtered data is empty; cannot analyze connectivity.")
        return set(), set(), set()

    var_by_process = (
        df.groupby("process_code")["variable"]
        .apply(lambda s: set(v.upper() for v in s))
        .to_dict()
    )
    both_io: set = set()
    only_in: set = set()
    only_out: set = set()

    for proc, vars_set in var_by_process.items():
        has_in = "VAR_FIN" in vars_set
        has_out = "VAR_FOUT" in vars_set
        if has_in and has_out:
            both_io.add(proc)
        elif has_in:
            only_in.add(proc)
        elif has_out:
            only_out.add(proc)

    logger.info("Processes with both inflows and outflows: %d", len(both_io))
    if only_in:
        logger.info(
            "Warning: Processes with inflows only (potentially isolated): %d",
            len(only_in),
        )
    if only_out:
        logger.info(
            "Warning: Processes with outflows only (potentially isolated): %d",
            len(only_out),
        )
    return both_io, only_in, only_out


def net_bidirectional_links(df: pd.DataFrame) -> pd.DataFrame:
    """Net bidirectional links between the same process and commodity."""
    if df.empty:
        return df

    d = df.copy()
    d["var_u"] = d["variable"].str.upper()
    d["signed"] = d.apply(
        lambda r: r["value"]
        if r["var_u"] == "VAR_FOUT"
        else (-r["value"] if r["var_u"] == "VAR_FIN" else 0.0),
        axis=1,
    )

    agg = d.groupby(
        ["year", "region", "process_code", "commodity_code"], as_index=False
    )["signed"].sum()
    agg = agg[agg["signed"] != 0]
    if agg.empty:
        cols = [
            "year",
            "region",
            "variable",
            "commodity_code",
            "commodity",
            "process_code",
            "process",
            "value",
        ]
        return pd.DataFrame(columns=cols)

    agg["variable"] = agg["signed"].apply(
        lambda v: "VAR_FOut" if v > 0 else "VAR_FIn"
    )
    agg["value"] = agg["signed"].abs()
    agg = agg.drop(columns=["signed"])

    comm_map = (
        d[["commodity_code", "commodity"]]
        .dropna()
        .drop_duplicates()
        .set_index("commodity_code")["commodity"]
        .to_dict()
    )
    proc_map = (
        d[["process_code", "process"]]
        .dropna()
        .drop_duplicates()
        .set_index("process_code")["process"]
        .to_dict()
    )

    agg["commodity"] = agg["commodity_code"].map(lambda c: comm_map.get(c, c))
    agg["process"] = agg["process_code"].map(lambda p: proc_map.get(p, p))
    return agg[
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


def _find_case_insensitive_column(df: pd.DataFrame, target_name: str) -> str | None:
    for c in df.columns:
        if c.strip().lower() == str(target_name).strip().lower():
            return c
    return None


def _majority_or_first(values: Iterable) -> str | None:
    vals = [
        str(v).strip()
        for v in values
        if str(v).strip() and str(v).strip().lower() != "nan"
    ]
    if not vals:
        return None
    counts: dict[str, int] = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    return sorted(counts.items(), key=lambda x: (-x[1], x[0]))[0][0]


def _slugify(name: str) -> str:
    s = (name or "").strip().lower()
    out: list[str] = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
        elif ch in [" ", "-", "/", "(", ")", "&"]:
            out.append("_")
    slug = "".join(out)
    while "__" in slug:
        slug = slug.replace("__", "_")
    return slug.strip("_") or "other"


def apply_mapping_based_process_clustering(
    df: pd.DataFrame,
    processes_df: pd.DataFrame,
    agg_column_name: str,
    process_unit_col: str | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Replace processes by aggregated process according to a mapping column."""
    if df.empty:
        return df, {}

    agg_col = _find_case_insensitive_column(processes_df, agg_column_name)
    if agg_col is None:
        logger.info(
            "Aggregation column '%s' not found in process mapping; skipping clustering.",
            agg_column_name,
        )
        return df, {}

    if "Process" not in processes_df.columns:
        raise KeyError("Process mapping DataFrame must contain a 'Process' column.")

    proc_to_cluster_code: dict[str, str] = {}
    cluster_code_to_name: dict[str, str] = {}

    for _, row in processes_df.iterrows():
        pcode = str(row["Process"]).strip()
        raw_label = str(row.get(agg_col, "")).strip()
        if raw_label and raw_label.lower() != "nan":
            cluster_code = f"AGG_{_slugify(raw_label)}"
            cluster_name = raw_label
        else:
            cluster_code = pcode
            cluster_name = str(row.get("Description", pcode))
        proc_to_cluster_code[pcode] = cluster_code
        if cluster_code not in cluster_code_to_name:
            cluster_code_to_name[cluster_code] = cluster_name

    aggregated_process_unit_map: dict = {}
    if process_unit_col is not None and process_unit_col in processes_df.columns:
        processes_df = processes_df.copy()
        processes_df["_cluster_code_tmp"] = processes_df["Process"].map(
            lambda p: proc_to_cluster_code.get(str(p).strip(), str(p).strip())
        )
        grouped = (
            processes_df.groupby("_cluster_code_tmp")[process_unit_col]
            .apply(list)
            .to_dict()
        )
        for cc, vals in grouped.items():
            chosen = _majority_or_first(vals)
            if chosen:
                aggregated_process_unit_map[cc] = chosen

    dfc = df.copy()
    dfc["process_code"] = dfc["process_code"].astype(str).map(
        lambda p: proc_to_cluster_code.get(p, p)
    )
    new_names = dfc["process_code"].map(lambda p: cluster_code_to_name.get(p))
    dfc["process"] = new_names.where(new_names.notna(), dfc["process"])
    return dfc, aggregated_process_unit_map


def read_commodity_mapping_table(mapping_file: Path | str) -> pd.DataFrame:
    """Read and normalize the commodity mapping CSV."""
    mapping_file = Path(mapping_file)
    if not mapping_file.exists():
        return pd.DataFrame(
            columns=[
                "pypsa",
                "times",
                "upstream_commodity",
                "upstream_process",
                "sector",
                "comment",
            ]
        )

    df = pd.read_csv(mapping_file, engine="python")
    cols_map = {c: c.strip().lower().replace("\ufeff", "") for c in df.columns}
    df = df.rename(columns=cols_map)

    def get(col_candidates: list[str]) -> str | None:
        for c in col_candidates:
            if c in df.columns:
                return c
        return None

    pypsa_col = get(["cluster"])
    times_col = get(["times commodity", "times_commodity", "times", "commodity"])
    desc_col = get(["description", "desc"])
    unit_col = get(["unit"])
    upst_comm_col = get(
        ["upstream commodity", "uspstream_commodity", "upstream_commodity"]
    )
    upst_proc_col = get(["upstream process", "upstream_process"])
    sector_col = get(["sector (com_in)", "sector"])
    comment_col = get(["comment", "comments"])

    n = len(df)

    def series_or_empty(col_name: str | None) -> pd.Series:
        if col_name is not None and col_name in df.columns:
            return df[col_name]
        return pd.Series([""] * n)

    out = pd.DataFrame(
        {
            "pypsa": series_or_empty(pypsa_col),
            "times": series_or_empty(times_col),
            "description": series_or_empty(desc_col),
            "unit": series_or_empty(unit_col),
            "upstream_commodity": series_or_empty(upst_comm_col),
            "upstream_process": series_or_empty(upst_proc_col),
            "sector": series_or_empty(sector_col),
            "comment": series_or_empty(comment_col),
        }
    )
    for c in out.columns:
        out[c] = out[c].apply(
            lambda x: str(x).strip() if pd.notna(x) and str(x).strip().lower() != "nan" else ""
        )
    return out[out["times"] != ""]


def build_commodity_groups_from_mapping(
    mapping_df: pd.DataFrame, energy_commodity_codes: set[str]
) -> tuple[dict, dict]:
    """Group TIMES commodity codes into PYPSA carriers."""
    mapping_df = mapping_df.copy()
    mapping_df["pypsa"] = mapping_df["pypsa"].astype(str).map(lambda s: s.strip())
    mapping_df["times"] = mapping_df["times"].astype(str).map(lambda s: s.strip())

    times_to_pypsa = {
        row["times"]: row["pypsa"]
        for _, row in mapping_df.iterrows()
        if row["times"]
    }

    missing = [c for c in energy_commodity_codes if c not in times_to_pypsa]
    if missing:
        logger.warning(
            "%d energy commodities present in data but not in mapping; leaving ungrouped.",
            len(missing),
        )
        for code in missing:
            times_to_pypsa[code] = code

    groups: dict = {}
    for code, pypsa_name in times_to_pypsa.items():
        gid = f"PYPSA_{_slugify(pypsa_name)}"
        if gid not in groups:
            groups[gid] = {
                "name": pypsa_name,
                "type": "commodity_group_pypsa",
                "members": [],
            }
        groups[gid]["members"].append(code)

    commodity_to_group = {
        m: gid for gid, info in groups.items() for m in info["members"]
    }
    groups_info = {
        gid: {
            "name": info["name"],
            "type": info["type"],
            "members": sorted(info["members"]),
        }
        for gid, info in groups.items()
    }
    return commodity_to_group, groups_info


def apply_commodity_grouping(
    df: pd.DataFrame,
    commodity_to_group: dict,
    groups_info: dict,
    commodities_df: pd.DataFrame,
) -> pd.DataFrame:
    """Replace commodity codes by grouped category ids."""
    if df.empty or not commodity_to_group:
        return df

    dfg = df.copy()
    if "commodity_code_orig" not in dfg.columns:
        dfg["commodity_code_orig"] = dfg["commodity_code"]
    dfg["commodity_code"] = dfg["commodity_code"].map(
        lambda c: commodity_to_group.get(c, c)
    )

    comm_desc = commodities_df.set_index("Commodity")["Description"].to_dict()
    name_map = {
        **comm_desc,
        **{gid: info["name"] for gid, info in groups_info.items()},
    }
    dfg["commodity"] = dfg["commodity_code"].map(lambda c: name_map.get(c, c))
    return dfg


def load_extraction_rules(extraction_rules_file: Path | str) -> dict:
    """Load demand extraction rules from CSV."""
    df = pd.read_csv(extraction_rules_file)
    rules: dict = {}
    for _, row in df.iterrows():
        category = row["category"].strip()
        var_type = row["var_type"].strip()
        filter_type = row["filter_type"].strip()

        filters: list | list[tuple] = []
        if filter_type == "combined":
            for i in [1, 2, 3]:
                f_field = f"filter_field_{i}"
                f_values = f"filter_values_{i}"
                if f_field in row and f_values in row and pd.notna(row[f_values]):
                    field = str(row[f_field]).strip()
                    values = [
                        v.strip()
                        for v in str(row[f_values]).split(";")
                        if v.strip()
                    ]
                    filters.append((field, values))
        else:
            f_values = [
                v.strip()
                for v in str(row.get("filter_values_1", "")).split(";")
                if v.strip()
            ]
            filters = f_values

        rules[category] = (var_type, filter_type, filters)
    return rules


def _build_commodity_pypsa_map(
    mapping_file: Path, commodities_mapping_df: pd.DataFrame
) -> dict[str, str]:
    """Build TIMES commodity → PyPSA energy carrier mapping."""
    commodity_pypsa_map: dict[str, str] = {}
    if not mapping_file.exists():
        return commodity_pypsa_map

    raw_mapping = pd.read_csv(mapping_file, engine="python")
    cols_map = {c: c.strip().lower().replace("\ufeff", "") for c in raw_mapping.columns}
    raw_mapping = raw_mapping.rename(columns=cols_map)

    if (
        "pypsa energy carrier" in raw_mapping.columns
        and "times commodity" in raw_mapping.columns
    ):
        for _, row in raw_mapping.iterrows():
            comm = str(row["times commodity"]).strip()
            pypsa = str(row.get("pypsa energy carrier", "")).strip()
            if pypsa and pypsa.lower() not in ["nan", ""]:
                commodity_pypsa_map[comm] = pypsa
    else:
        logger.warning(
            "PyPSA Energy Carrier column not found; using Cluster column as fallback."
        )
        if "times" in commodities_mapping_df.columns and "pypsa" in commodities_mapping_df.columns:
            for _, row in commodities_mapping_df.iterrows():
                comm = str(row["times"]).strip()
                pypsa = str(row.get("pypsa", "")).strip()
                if pypsa and pypsa.lower() not in ["nan", ""]:
                    commodity_pypsa_map[comm] = pypsa
    return commodity_pypsa_map


def _process_agg_column(df: pd.DataFrame) -> str:
    """
    Column used by extraction_rules ``process_agg`` filters.

    Canonical name is ``process_agg`` (Aggregation Level 2 labels).
    Older code stored those labels in ``agg_level_1`` — accept either.
    """
    if "process_agg" in df.columns:
        return "process_agg"
    return "agg_level_1"


def _apply_extraction_rule(
    year_df: pd.DataFrame,
    var_type: str,
    filter_type: str,
    filter_values,
    apply_netting: bool,
) -> pd.DataFrame:
    """Apply a single extraction rule to year data."""
    if apply_netting:
        filtered_df = year_df.copy()
    elif var_type == "both":
        filtered_df = year_df.copy()
    else:
        filtered_df = year_df[year_df["variable"].str.upper() == var_type].copy()

    agg_col = _process_agg_column(filtered_df)

    if filter_type == "process_agg":
        filtered_df = filtered_df[filtered_df[agg_col].isin(filter_values)]
    elif filter_type == "pypsa_carrier":
        filtered_df = filtered_df[filtered_df["pypsa_carrier"].isin(filter_values)]
    elif filter_type == "commodity":
        filtered_df = filtered_df[filtered_df["commodity"].isin(filter_values)]
    elif filter_type == "commodity_code":
        filtered_df = filtered_df[filtered_df["commodity_code"].isin(filter_values)]
    elif filter_type == "combined":
        for sub_filter_type, sub_filter_values in filter_values:
            if sub_filter_type == "process_agg":
                filtered_df = filtered_df[
                    filtered_df[agg_col].isin(sub_filter_values)
                ]
            elif sub_filter_type == "pypsa_carrier":
                filtered_df = filtered_df[
                    filtered_df["pypsa_carrier"].isin(sub_filter_values)
                ]
            elif sub_filter_type == "commodity":
                filtered_df = filtered_df[
                    filtered_df["commodity"].isin(sub_filter_values)
                ]
            elif sub_filter_type == "commodity_code":
                filtered_df = filtered_df[
                    filtered_df["commodity_code"].isin(sub_filter_values)
                ]

    if apply_netting and not filtered_df.empty:
        netted_df = filtered_df.copy()
        netted_df["process_code_orig"] = netted_df["process_code"]
        netted_df["process_code"] = netted_df[agg_col]
        netted_df = net_bidirectional_links(netted_df)
        if var_type == "VAR_FIN":
            netted_df = netted_df[netted_df["variable"].str.upper() == "VAR_FIN"]
        elif var_type == "VAR_FOUT":
            netted_df = netted_df[netted_df["variable"].str.upper() == "VAR_FOUT"]
        filtered_df = netted_df

    return filtered_df


def _adjust_road_rail_totals(results_df: pd.DataFrame) -> pd.DataFrame:
    """Subtract rail from road electricity/total road categories."""
    results_df = results_df.copy()
    road_raw = results_df.loc[
        results_df["category"] == "electricity road", "TWh"
    ].iloc[0]
    rail_raw = results_df.loc[
        results_df["category"] == "electricity rail", "TWh"
    ].iloc[0]
    net_road_twh = road_raw - rail_raw
    results_df.loc[results_df["category"] == "electricity road", "TWh"] = net_road_twh
    results_df.loc[results_df["category"] == "electricity road", "PJ"] = (
        net_road_twh / PJ_TO_TWH
    )

    road_tot = results_df.loc[results_df["category"] == "total road", "TWh"].iloc[0]
    rail_tot = results_df.loc[results_df["category"] == "total rail", "TWh"].iloc[0]
    tot_road_twh = road_tot - rail_tot
    results_df.loc[results_df["category"] == "total road", "TWh"] = tot_road_twh
    results_df.loc[results_df["category"] == "total road", "PJ"] = (
        tot_road_twh / PJ_TO_TWH
    )
    return results_df


def extract_heating_capacities(
    raw_flows_df: pd.DataFrame,
    processes_df: pd.DataFrame,
    horizon: int,
    heating_capacities_path: Path | str,
) -> pd.DataFrame:
    """Extract and save heating technology capacities for one horizon."""
    heating_capacities_path = Path(heating_capacities_path)
    heating_capacities_path.parent.mkdir(parents=True, exist_ok=True)

    mapping = processes_df.copy()
    tech_col = (
        "Technology (Process)"
        if "Technology (Process)" in mapping.columns
        else "Process"
    )
    agg_col = "Aggregation Level 2"

    filtered_capacities = raw_flows_df.loc[
        (raw_flows_df["year"] == horizon)
        & (raw_flows_df["variable"].isin(["VAR_Cap", "VAR_Ncap"]))
    ].copy()
    map_dict = mapping.set_index(tech_col)[agg_col].to_dict()
    filtered_capacities["mapped_process"] = filtered_capacities["process_code"].map(
        map_dict
    )
    aggregated = (
        filtered_capacities.groupby("mapped_process", dropna=False)
        .sum(numeric_only=True)
        .drop(columns=["year"])
    )
    aggregated = (aggregated * 1000).round(2)
    aggregated = aggregated[
        aggregated.index.str.contains(
            "boiler|heat pump|stove|thermal|heater", case=False, na=False
        )
    ]
    aggregated["year"] = horizon
    aggregated.to_csv(heating_capacities_path, index=True)
    logger.info("Saved heating capacities to %s", heating_capacities_path)
    return aggregated


def extract_demands_for_horizon(
    annual_values_df: pd.DataFrame,
    processes_df: pd.DataFrame,
    commodities_mapping_df: pd.DataFrame,
    extraction_rules: dict,
    commodity_mapping_file: Path,
    horizon: int,
    wallon_demands_path: Path | str,
    apply_netting: bool = True,
) -> pd.DataFrame:
    """Extract PyPSA demand categories for a single planning horizon."""
    wallon_demands_path = Path(wallon_demands_path)
    wallon_demands_path.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Extracting PyPSA demands for horizon %d", horizon)
    if apply_netting:
        logger.info(
            "Netting enabled to remove internal transfers within aggregated processes."
        )

    # process_agg = Aggregation Level 2 labels (used by extraction_rules.csv).
    # Also mirrored to agg_level_1 for backward compatibility with older callers.
    process_agg_map: dict[str, str] = {}
    if "Process" in processes_df.columns and "Aggregation Level 2" in processes_df.columns:
        for _, row in processes_df.iterrows():
            proc = str(row["Process"]).strip()
            agg = str(row.get("Aggregation Level 2", "")).strip()
            if agg and agg.lower() not in ["nan", ""]:
                process_agg_map[proc] = agg

    commodity_pypsa_map = _build_commodity_pypsa_map(
        commodity_mapping_file, commodities_mapping_df
    )

    year_df = annual_values_df[
        (annual_values_df["year"] == horizon)
        & (annual_values_df["variable"].str.upper().isin(["VAR_FIN", "VAR_FOUT"]))
    ].copy()

    if year_df.empty:
        logger.warning("No flow data for horizon %d", horizon)
        return pd.DataFrame(columns=["category", "TWh", "PJ", "year"])

    year_df["process_agg"] = year_df["process_code"].map(process_agg_map)
    year_df["agg_level_1"] = year_df["process_agg"]  # legacy alias
    year_df["pypsa_carrier"] = year_df["commodity_code"].map(commodity_pypsa_map)

    results = []
    for category, (var_type, filter_type, filter_values) in extraction_rules.items():
        filtered_df = _apply_extraction_rule(
            year_df, var_type, filter_type, filter_values, apply_netting
        )
        total_pj = filtered_df["value"].sum()
        results.append(
            {
                "category": category,
                "TWh": total_pj * PJ_TO_TWH,
                "PJ": total_pj,
            }
        )

    results_df = _adjust_road_rail_totals(pd.DataFrame(results))
    results_df["year"] = horizon
    results_df.to_csv(wallon_demands_path, index=False)
    logger.info("Saved walloon demands to %s", wallon_demands_path)
    return results_df


def build_sankey(
    df: pd.DataFrame,
    output_html_file: Path | str,
    year: int,
    flow_threshold: float = 0.0,
    process_unit_map: dict | None = None,
):
    """Build and save a Sankey diagram from filtered annual flows."""
    output_html_file = Path(output_html_file)
    output_html_file.parent.mkdir(parents=True, exist_ok=True)

    if df.empty:
        logger.info("No data to plot after filtering.")
        return None

    df = df.copy()
    df["source"] = df.apply(
        lambda r: r["commodity_code"]
        if r["variable"].upper() == "VAR_FIN"
        else r["process_code"],
        axis=1,
    )
    df["target"] = df.apply(
        lambda r: r["process_code"]
        if r["variable"].upper() == "VAR_FIN"
        else r["commodity_code"],
        axis=1,
    )

    links_df = df.groupby(["source", "target"], as_index=False)["value"].sum()
    if flow_threshold > 0:
        links_df = links_df[links_df["value"] > flow_threshold]

    if links_df.empty:
        logger.info("No links above the threshold to plot.")
        return None

    nodes = pd.concat([links_df["source"], links_df["target"]]).unique().tolist()
    node_index = {n: i for i, n in enumerate(nodes)}

    commodity_desc = (
        df[["commodity_code", "commodity"]]
        .dropna()
        .drop_duplicates()
        .set_index("commodity_code")["commodity"]
        .to_dict()
    )
    process_desc = (
        df[["process_code", "process"]]
        .dropna()
        .drop_duplicates()
        .set_index("process_code")["process"]
        .to_dict()
    )

    labels = [
        commodity_desc.get(n, process_desc.get(n, n)) for n in nodes
    ]

    node_label_map = {n: lbl for n, lbl in zip(nodes, labels)}
    tooltips = []
    for _, row in links_df.iterrows():
        s, t, v = row["source"], row["target"], float(row["value"])
        src = node_label_map.get(s, str(s))
        tgt = node_label_map.get(t, str(t))
        comm_label = commodity_desc.get(s) or commodity_desc.get(t)
        proc_code = s if s in process_desc else (t if t in process_desc else None)
        unit = (
            process_unit_map.get(proc_code)
            if process_unit_map is not None and proc_code
            else None
        )
        unit_str = unit if unit and isinstance(unit, str) and unit.strip() else "PJ"
        if comm_label:
            tooltip = (
                f"{src} → {tgt}<br>Commodity: {comm_label}<br>Value: {v:.2f} {unit_str}"
            )
        else:
            tooltip = f"{src} → {tgt}<br>Value: {v:.2f} {unit_str}"
        tooltips.append(tooltip)

    left_nodes = {n for n in nodes if n in commodity_desc}
    right_nodes = {n for n in nodes if n in process_desc}
    n_max_col = max(len(left_nodes), len(right_nodes)) or 1
    dyn_pad = max(4, min(20, int(300 / n_max_col)))
    dyn_thickness = max(10, min(30, int(600 / n_max_col)))

    fig = go.Figure(
        data=[
            go.Sankey(
                node=dict(
                    pad=dyn_pad,
                    thickness=dyn_thickness,
                    line=dict(color="black", width=0.5),
                    label=labels,
                ),
                link={
                    "source": links_df["source"].map(node_index).tolist(),
                    "target": links_df["target"].map(node_index).tolist(),
                    "value": links_df["value"].tolist(),
                    "customdata": tooltips,
                    "hovertemplate": "%{customdata}<extra></extra>",
                },
            )
        ]
    )
    fig.update_layout(
        title_text=f"Energy Flow Diagram - {year} (PJ)", font_size=10
    )
    fig.write_html(output_html_file)
    logger.info("Saved Sankey diagram to %s", output_html_file)
    return fig


@dataclass
class _Metadata:
    processes_df: pd.DataFrame
    mapping_df: pd.DataFrame
    commodities_df: pd.DataFrame
    process_unit_col: str | None
    process_unit_map: dict
    commodity_mapping_file: Path
    process_mapping_file: Path
    extraction_rules_file: Path


def _mapping_paths(mappings_dir: Path) -> tuple[Path, Path, Path]:
    mappings_dir = Path(mappings_dir)
    return (
        mappings_dir / "mapping_commodities.csv",
        mappings_dir / "mapping_processes.csv",
        mappings_dir / "extraction_rules.csv",
    )


def load_metadata(mappings_dir: Path | str) -> _Metadata:
    """Load process/commodity mappings from a directory."""
    mappings_dir = Path(mappings_dir)
    commodity_file, process_file, rules_file = _mapping_paths(mappings_dir)

    processes_df = pd.read_csv(process_file)
    if "Process" not in processes_df.columns and "Technology (Process)" in processes_df.columns:
        processes_df = processes_df.rename(columns={"Technology (Process)": "Process"})
    if "Description" not in processes_df.columns:
        for c in processes_df.columns:
            if c.strip().lower() == "description":
                processes_df = processes_df.rename(columns={c: "Description"})
                break

    process_unit_col = None
    for c in processes_df.columns:
        if c.strip().lower() in ["activity unit", "activity_unit", "unit"]:
            process_unit_col = c
            break
    process_unit_map = (
        processes_df.set_index("Process")[process_unit_col].to_dict()
        if process_unit_col
        else {}
    )

    mapping_df = read_commodity_mapping_table(commodity_file)
    if "description" not in mapping_df.columns:
        try:
            raw_map = pd.read_csv(commodity_file, engine="python")
            if "Description" in raw_map.columns and "TIMES commodity" in raw_map.columns:
                mapping_df = mapping_df.merge(
                    raw_map[["TIMES commodity", "Description"]].rename(
                        columns={"TIMES commodity": "times"}
                    ),
                    on="times",
                    how="left",
                )
                mapping_df = mapping_df.rename(columns={"Description": "description"})
        except Exception:
            pass

    commodities_df = pd.DataFrame(
        {
            "Commodity": mapping_df["times"],
            "Description": mapping_df["description"]
            if "description" in mapping_df.columns
            else "",
        }
    )

    return _Metadata(
        processes_df=processes_df,
        mapping_df=mapping_df,
        commodities_df=commodities_df,
        process_unit_col=process_unit_col,
        process_unit_map=process_unit_map,
        commodity_mapping_file=commodity_file,
        process_mapping_file=process_file,
        extraction_rules_file=rules_file,
    )


def prepare_annual_values(
    vd_file: Path | str,
    metadata: _Metadata,
    config: PipelineConfig | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Load .vd file and return (raw_flows_df, annual_values_df with descriptions)."""
    config = config or PipelineConfig()
    raw_flows_df = load_raw_records(vd_file, start_year=config.start_year)
    if raw_flows_df.empty:
        return raw_flows_df, raw_flows_df

    annual_values_df = aggregate_to_annual(raw_flows_df)
    annual_values_df = (
        annual_values_df.merge(
            metadata.commodities_df.rename(
                columns={"Commodity": "commodity_code", "Description": "commodity"}
            ),
            on="commodity_code",
            how="left",
        )
        .merge(
            metadata.processes_df.rename(
                columns={"Process": "process_code", "Description": "process"}
            ),
            on="process_code",
            how="left",
        )
    )
    annual_values_df["commodity"] = annual_values_df["commodity"].fillna(
        annual_values_df["commodity_code"]
    )
    annual_values_df["process"] = annual_values_df["process"].fillna(
        annual_values_df["process_code"]
    )
    ordered_cols = [
        "year",
        "region",
        "variable",
        "commodity_code",
        "commodity",
        "process_code",
        "process",
        "value",
    ]
    for col in ordered_cols:
        if col not in annual_values_df.columns:
            annual_values_df[col] = None
    return raw_flows_df, annual_values_df[ordered_cols]


def _sankey_config(config: PipelineConfig) -> tuple[bool, bool, str | None, bool]:
    if config.enable_process_clustering:
        netting = config.process_cluster_column == "Aggregation Level 2"
        return True, config.group_commodities, config.process_cluster_column, netting
    return False, config.group_commodities, None, False


def _prepare_sankey_df(
    annual_values_df: pd.DataFrame,
    metadata: _Metadata,
    year: int,
    config: PipelineConfig,
    out_dir: Path | None = None,
) -> tuple[pd.DataFrame, dict]:
    """Filter, cluster, and net flows for Sankey rendering."""
    enable_clustering, group_commodities, cluster_col, netting = _sankey_config(config)

    filtered_df, energy_codes = filter_for_sankey(
        annual_values_df,
        year=year,
        mapping_df=metadata.mapping_df,
        processes_df=metadata.processes_df,
    )
    if filtered_df.empty:
        return filtered_df, metadata.process_unit_map

    if enable_clustering and cluster_col:
        preview_clustered, _ = apply_mapping_based_process_clustering(
            filtered_df,
            metadata.processes_df,
            agg_column_name=cluster_col,
            process_unit_col=metadata.process_unit_col,
        )
        filtered_df = filtered_df.copy()
        filtered_df["clustered_process_code"] = preview_clustered["process_code"]
        filtered_df["clustered_process"] = preview_clustered["process"]
    else:
        filtered_df = filtered_df.copy()
        filtered_df["clustered_process_code"] = filtered_df["process_code"]
        filtered_df["clustered_process"] = filtered_df["process"]

    commodity_to_group = None
    groups_info = None
    if group_commodities:
        commodity_to_group, groups_info = build_commodity_groups_from_mapping(
            metadata.mapping_df, energy_codes
        )
        group_name_map = (
            {gid: info["name"] for gid, info in groups_info.items()}
            if groups_info
            else {}
        )
        comm_name_map = filtered_df.set_index("commodity_code")["commodity"].to_dict()
        filtered_df["grouped_commodity_code"] = filtered_df["commodity_code"].map(
            lambda c: commodity_to_group.get(c, c)
        )
        filtered_df["grouped_commodity"] = filtered_df["grouped_commodity_code"].map(
            lambda gid: group_name_map.get(gid, comm_name_map.get(gid, gid))
        )

        if out_dir is not None and groups_info:
            out_dir.mkdir(parents=True, exist_ok=True)
            suffix = "_clustered" if enable_clustering else ""
            with (out_dir / f"sankey_commodity_groups_{year}.json").open("w") as f:
                json.dump(groups_info, f, indent=2)
            pd.DataFrame(
                [
                    {
                        "group_id": gid,
                        "name": info["name"],
                        "type": info["type"],
                        "members": ";".join(info["members"]),
                    }
                    for gid, info in groups_info.items()
                ]
            ).to_csv(out_dir / f"sankey_commodity_groups_{year}.csv", index=False)
            filtered_df = apply_commodity_grouping(
                filtered_df, commodity_to_group, groups_info, metadata.commodities_df
            )
    else:
        filtered_df["grouped_commodity_code"] = filtered_df["commodity_code"]
        filtered_df["grouped_commodity"] = filtered_df["commodity"]

    if out_dir is not None:
        suffix = "_clustered" if enable_clustering else ""
        filtered_df.to_csv(
            out_dir / f"annual_flows_{year}_energy{suffix}.csv", index=False
        )

    analyze_process_connectivity(filtered_df)
    final_unit_map = metadata.process_unit_map

    if enable_clustering and cluster_col:
        logger.info("Applying process clustering using column: '%s'", cluster_col)
        sankey_df, aggregated_unit_map = apply_mapping_based_process_clustering(
            filtered_df,
            metadata.processes_df,
            agg_column_name=cluster_col,
            process_unit_col=metadata.process_unit_col,
        )
        final_unit_map = {**metadata.process_unit_map, **aggregated_unit_map}
    else:
        sankey_df = filtered_df

    if netting:
        logger.info("Applying netting to bidirectional links...")
        sankey_df = net_bidirectional_links(sankey_df)

    return sankey_df, final_unit_map


def generate_sankey(
    vd_file: Path | str,
    mappings_dir: Path | str,
    year: int,
    out_dir: Path | str,
    config: PipelineConfig | None = None,
) -> None:
    """Generate Sankey diagram and auxiliary CSVs for one year."""
    config = config or PipelineConfig()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    metadata = load_metadata(mappings_dir)
    raw_flows_df, annual_values_df = prepare_annual_values(vd_file, metadata, config)
    if annual_values_df.empty:
        logger.warning("No data in %s; skipping Sankey.", vd_file)
        return

    suffix = "_clustered" if config.enable_process_clustering else ""
    annual_values_df.to_csv(out_dir / f"annual_values{suffix}.csv", index=False)

    sankey_df, unit_map = _prepare_sankey_df(
        annual_values_df, metadata, year, config, out_dir=out_dir
    )
    if sankey_df.empty:
        logger.warning("Filtered dataset for Sankey is empty; skipping.")
        return

    html_path = out_dir / f"bau_sankey_{year}_pj{suffix}.html"
    build_sankey(
        sankey_df,
        html_path,
        year=year,
        flow_threshold=config.flow_threshold,
        process_unit_map=unit_map,
    )


def export_horizon(
    vd_file: Path | str,
    mappings_dir: Path | str,
    horizon: int,
    wallon_demands_path: Path | str,
    heating_capacities_path: Path | str,
    sankey_dir: Path | str | None = None,
    emit_sankey: bool = False,
    config: PipelineConfig | None = None,
) -> None:
    """
    Export PyPSA demands and heating capacities for one planning horizon.

    Optionally generate a Sankey diagram when ``emit_sankey`` is True and
    ``sankey_dir`` is provided.
    """
    config = config or PipelineConfig()
    metadata = load_metadata(mappings_dir)
    extraction_rules = load_extraction_rules(metadata.extraction_rules_file)

    raw_flows_df, annual_values_df = prepare_annual_values(vd_file, metadata, config)
    if annual_values_df.empty:
        logger.warning("No data in %s; nothing to export.", vd_file)
        return

    extract_heating_capacities(
        raw_flows_df,
        metadata.processes_df,
        horizon,
        heating_capacities_path,
    )
    extract_demands_for_horizon(
        annual_values_df,
        metadata.processes_df,
        metadata.mapping_df,
        extraction_rules,
        metadata.commodity_mapping_file,
        horizon,
        wallon_demands_path,
        apply_netting=config.apply_netting,
    )

    if emit_sankey and sankey_dir is not None:
        generate_sankey(vd_file, mappings_dir, horizon, sankey_dir, config=config)


def export_all_horizons(
    vd_file: Path | str,
    mappings_dir: Path | str,
    horizons: Iterable[int],
    out_dir: Path | str,
    emit: EmitMode = "all",
    config: PipelineConfig | None = None,
) -> None:
    """
    Export demands (and optionally Sankey) for multiple planning horizons.

    ``emit`` controls output: ``demands``, ``sankey``, or ``all``.
    """
    config = config or PipelineConfig()
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    metadata = load_metadata(mappings_dir)
    extraction_rules = load_extraction_rules(metadata.extraction_rules_file)

    raw_flows_df, annual_values_df = prepare_annual_values(vd_file, metadata, config)
    if annual_values_df.empty:
        logger.warning("No data in %s; nothing to export.", vd_file)
        return

    horizons = list(horizons)
    if emit in ("demands", "all"):
        for horizon in horizons:
            wallon_path = out_dir / f"wallon_demands_{horizon}.csv"
            results_df = extract_demands_for_horizon(
                annual_values_df,
                metadata.processes_df,
                metadata.mapping_df,
                extraction_rules,
                metadata.commodity_mapping_file,
                horizon,
                wallon_path,
                apply_netting=config.apply_netting,
            )
            if not results_df.empty:
                shutil.copy2(wallon_path, out_dir / f"pypsa_demands_{horizon}.csv")
        for horizon in horizons:
            extract_heating_capacities(
                raw_flows_df,
                metadata.processes_df,
                horizon,
                out_dir / f"heating_capacities_{horizon}.csv",
            )

    if emit in ("sankey", "all"):
        for horizon in horizons:
            generate_sankey(vd_file, mappings_dir, horizon, out_dir, config=config)


_MAPPING_FILES = (
    "mapping_commodities.csv",
    "mapping_processes.csv",
    "extraction_rules.csv",
)


def _ensure_times_vd(coupling_dir: Path, vd_file: Path) -> Path:
    """Place the scenario .vd under ``coupling_dir/times/`` if not already present."""
    times_dir = coupling_dir / "times"
    times_dir.mkdir(parents=True, exist_ok=True)
    dest_vd = times_dir / "scenario.vd"
    if not dest_vd.exists():
        vd_resolved = vd_file.resolve()
        try:
            dest_vd.symlink_to(vd_resolved)
        except OSError:
            shutil.copy2(vd_resolved, dest_vd)
    return dest_vd


def _ensure_coupling_mappings(
    coupling_dir: Path, mappings_dir: Path
) -> tuple[Path, str]:
    """Copy bundled mapping CSVs into ``coupling_dir/mappings/`` when missing."""
    coupling_mappings = coupling_dir / "mappings"
    coupling_mappings.mkdir(parents=True, exist_ok=True)
    for name in _MAPPING_FILES:
        dest = coupling_mappings / name
        if not dest.exists():
            shutil.copy2(mappings_dir / name, dest)
    return coupling_mappings, str(mappings_dir.resolve())


def export_coupling_dir(
    coupling_dir: Path | str,
    vd_file: Path | str,
    horizons: Iterable[int],
    mappings_dir: Path | str | None = None,
    config: PipelineConfig | None = None,
) -> None:
    """
    Export a soft-linking bundle for PyPSA-WAL under ``coupling_dir``.

    Layout::

        <coupling_dir>/
          times/scenario.vd
          mappings/{mapping_commodities,mapping_processes,extraction_rules}.csv
          pypsa_inputs/
            wallon_demands_{h}.csv
            pypsa_demands_{h}.csv
            heating_capacities_{h}.csv
            manifest.json
    """
    config = config or PipelineConfig()
    coupling_dir = Path(coupling_dir)
    vd_file = Path(vd_file)
    mappings_dir = Path(mappings_dir) if mappings_dir else default_mappings_dir()
    horizons = list(horizons)

    coupling_dir.mkdir(parents=True, exist_ok=True)
    scenario_vd = _ensure_times_vd(coupling_dir, vd_file)
    coupling_mappings, mappings_source = _ensure_coupling_mappings(
        coupling_dir, mappings_dir
    )

    pypsa_inputs = coupling_dir / "pypsa_inputs"
    pypsa_inputs.mkdir(parents=True, exist_ok=True)

    metadata = load_metadata(coupling_mappings)
    extraction_rules = load_extraction_rules(metadata.extraction_rules_file)

    raw_flows_df, annual_values_df = prepare_annual_values(
        vd_file, metadata, config
    )
    if annual_values_df.empty:
        logger.warning("No data in %s; nothing to export.", vd_file)
        return

    for horizon in horizons:
        wallon_path = pypsa_inputs / f"wallon_demands_{horizon}.csv"
        results_df = extract_demands_for_horizon(
            annual_values_df,
            metadata.processes_df,
            metadata.mapping_df,
            extraction_rules,
            metadata.commodity_mapping_file,
            horizon,
            wallon_path,
            apply_netting=config.apply_netting,
        )
        if not results_df.empty:
            shutil.copy2(wallon_path, pypsa_inputs / f"pypsa_demands_{horizon}.csv")

    for horizon in horizons:
        extract_heating_capacities(
            raw_flows_df,
            metadata.processes_df,
            horizon,
            pypsa_inputs / f"heating_capacities_{horizon}.csv",
        )

    try:
        library_version = version("times-pypsa")
    except Exception:
        library_version = LIBRARY_VERSION

    manifest = {
        "library_version": library_version,
        "vd_file": str(scenario_vd.resolve()),
        "vd_name": scenario_vd.name,
        "horizons": horizons,
        "mappings_source": mappings_source,
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "category_count": len(extraction_rules),
    }
    with (pypsa_inputs / "manifest.json").open("w") as f:
        json.dump(manifest, f, indent=2)
    logger.info("Wrote coupling manifest to %s", pypsa_inputs / "manifest.json")
