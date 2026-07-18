"""Typed TIMES annual energy-flow model built from .vd (+ optional .vdt)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from times_pypsa.pipeline import (
    PipelineConfig,
    _build_commodity_pypsa_map,
    aggregate_to_annual,
    load_metadata,
    load_raw_records,
    prepare_annual_values,
)

if TYPE_CHECKING:
    from times_pypsa.topology import Topology

logger = logging.getLogger(__name__)

FLOW_VARS = frozenset({"VAR_FIN", "VAR_FOUT"})
COMNET_VAR = "VAR_COMNET"


@dataclass
class TimesAnnualFlows:
    """
    Annual TIMES results enriched with process/commodity mappings.

    Columns on ``flows`` (energy VAR_FIn/VAR_FOut rows):
        year, region, variable, commodity_code, commodity, process_code, process,
        value, sector, agg_level_1, agg_level_2, process_agg, pypsa_carrier,
        commodity_sector (optional)
    """

    flows: pd.DataFrame
    raw: pd.DataFrame
    comnet: pd.DataFrame  # year, region, commodity_code, value
    mappings_dir: Path
    topology: Topology | None = None
    topology_mismatches: pd.DataFrame = field(default_factory=pd.DataFrame)

    @property
    def years(self) -> list[int]:
        if self.flows.empty:
            return []
        return sorted(int(y) for y in self.flows["year"].unique())

    def flows_for_year(self, year: int) -> pd.DataFrame:
        if self.flows.empty:
            return self.flows
        return self.flows[self.flows["year"] == year].copy()

    def energy_flows(self, year: int | None = None) -> pd.DataFrame:
        df = self.flows if year is None else self.flows_for_year(year)
        if df.empty:
            return df
        return df[df["variable"].str.upper().isin(FLOW_VARS)].copy()


def _process_column_map(processes_df: pd.DataFrame) -> dict[str, dict[str, str]]:
    """Map process_code → {sector, agg_level_1, agg_level_2, process_agg, process_type}."""
    out: dict[str, dict[str, str]] = {}
    if processes_df.empty or "Process" not in processes_df.columns:
        return out
    for _, row in processes_df.iterrows():
        proc = str(row["Process"]).strip()
        sector = str(row.get("Sector", "") or "").strip()
        l1 = str(row.get("Aggregation Level 1", "") or "").strip()
        l2 = str(row.get("Aggregation Level 2", "") or "").strip()
        ptype = str(row.get("Type", "") or "").strip()
        if l1.lower() in {"nan", ""}:
            l1 = ""
        if l2.lower() in {"nan", ""}:
            l2 = ""
        if sector.lower() in {"nan", ""}:
            sector = ""
        if ptype.lower() in {"nan", ""}:
            ptype = ""
        # process_agg is the label used by extraction_rules (historically
        # stored as agg_level_1 but taken from Aggregation Level 2).
        process_agg = l2 or l1
        out[proc] = {
            "sector": sector,
            "agg_level_1": l1,
            "agg_level_2": l2,
            "process_agg": process_agg,
            "process_type": ptype,
        }
    return out


def enrich_annual_flows(
    annual_df: pd.DataFrame,
    processes_df: pd.DataFrame,
    commodity_pypsa_map: dict[str, str],
    commodity_sectors: dict[str, str] | None = None,
) -> pd.DataFrame:
    """Attach sector / aggregation / pypsa_carrier columns to annual flow rows."""
    if annual_df.empty:
        return annual_df

    df = annual_df.copy()
    proc_map = _process_column_map(processes_df)
    commodity_sectors = commodity_sectors or {}

    df["sector"] = df["process_code"].map(
        lambda p: proc_map.get(str(p).strip(), {}).get("sector", "")
    )
    df["agg_level_1"] = df["process_code"].map(
        lambda p: proc_map.get(str(p).strip(), {}).get("agg_level_1", "")
    )
    df["agg_level_2"] = df["process_code"].map(
        lambda p: proc_map.get(str(p).strip(), {}).get("agg_level_2", "")
    )
    df["process_agg"] = df["process_code"].map(
        lambda p: proc_map.get(str(p).strip(), {}).get("process_agg", "")
    )
    df["process_type"] = df["process_code"].map(
        lambda p: proc_map.get(str(p).strip(), {}).get("process_type", "")
    )
    # Backward-compatible alias: extraction historically filtered on agg_level_1
    # meaning Aggregation Level 2. Keep process_agg as canonical; also mirror
    # process_agg into a dedicated extraction column used by rules.
    df["agg_level_1_for_rules"] = df["process_agg"]
    df["pypsa_carrier"] = df["commodity_code"].map(
        lambda c: commodity_pypsa_map.get(str(c).strip(), "")
    )
    df["commodity_sector"] = df["commodity_code"].map(
        lambda c: commodity_sectors.get(str(c).strip(), "")
    )
    return df


def extract_comnet(raw_or_annual: pd.DataFrame) -> pd.DataFrame:
    """Extract annual VAR_Comnet by (year, region, commodity_code)."""
    if raw_or_annual.empty:
        return pd.DataFrame(columns=["year", "region", "commodity_code", "value"])

    df = raw_or_annual[
        raw_or_annual["variable"].str.upper() == COMNET_VAR
    ].copy()
    if df.empty:
        return pd.DataFrame(columns=["year", "region", "commodity_code", "value"])

    # If timeslice still present, aggregate; else already annual
    group_cols = ["year", "region", "commodity_code"]
    if "timeslice" in df.columns:
        return df.groupby(group_cols, as_index=False)["value"].sum()
    return df.groupby(group_cols, as_index=False)["value"].sum()


def _load_commodity_sectors(commodity_mapping_file: Path) -> dict[str, str]:
    if not commodity_mapping_file.exists():
        return {}
    raw = pd.read_csv(commodity_mapping_file, engine="python")
    cols = {c: c.strip().lower().replace("\ufeff", "") for c in raw.columns}
    raw = raw.rename(columns=cols)
    code_col = "times commodity" if "times commodity" in raw.columns else None
    sector_col = "sector" if "sector" in raw.columns else None
    if not code_col or not sector_col:
        return {}
    out: dict[str, str] = {}
    for _, row in raw.iterrows():
        code = str(row[code_col]).strip()
        sector = str(row.get(sector_col, "") or "").strip()
        if code and sector and sector.lower() != "nan":
            out[code] = sector
    return out


def _check_topology_mismatches(
    flows: pd.DataFrame,
    topology: Topology,
) -> pd.DataFrame:
    """Return energy-flow rows whose (region, process, commodity, dir) is absent from .vdt."""
    if flows.empty or topology is None or topology.links.empty:
        return pd.DataFrame()

    keys = topology.as_key_set()
    energy = flows[flows["variable"].str.upper().isin(FLOW_VARS)].copy()
    if energy.empty:
        return pd.DataFrame()

    # Region-level aggregates use process_code '-' and are not in .vdt
    energy = energy[energy["process_code"].astype(str) != "-"]
    if energy.empty:
        return pd.DataFrame()

    def _mismatch(row) -> bool:
        direction = "IN" if str(row["variable"]).upper() == "VAR_FIN" else "OUT"
        # Topology may use RW while some flows use other regions; try exact match
        key = (
            str(row["region"]),
            str(row["process_code"]),
            str(row["commodity_code"]),
            direction,
        )
        if key in keys:
            return False
        # Fallback: ignore region (IMPEXP vs RW mismatches)
        for region in {str(row["region"]), "RW", "IMPEXP"}:
            if (
                region,
                str(row["process_code"]),
                str(row["commodity_code"]),
                direction,
            ) in keys:
                return False
        return True

    mask = energy.apply(_mismatch, axis=1)
    return energy.loc[mask].copy()


def load_times_annual_flows(
    vd_file: Path | str,
    mappings_dir: Path | str,
    *,
    vdt_file: Path | str | None = None,
    config: PipelineConfig | None = None,
    topology: Topology | None = None,
) -> TimesAnnualFlows:
    """
    Build an enriched annual flow model from a TIMES .vd file.

    If ``vdt_file`` (or ``topology``) is provided, also compute topology mismatches.
    """
    from times_pypsa.topology import Topology, load_topology

    config = config or PipelineConfig()
    mappings_dir = Path(mappings_dir)
    metadata = load_metadata(mappings_dir)
    raw, annual = prepare_annual_values(vd_file, metadata, config=config)

    commodity_pypsa_map = _build_commodity_pypsa_map(
        metadata.commodity_mapping_file, metadata.mapping_df
    )
    commodity_sectors = _load_commodity_sectors(metadata.commodity_mapping_file)

    flows = enrich_annual_flows(
        annual,
        metadata.processes_df,
        commodity_pypsa_map,
        commodity_sectors=commodity_sectors,
    )
    comnet = extract_comnet(raw if "timeslice" in raw.columns else annual)

    topo = topology
    if topo is None and vdt_file is not None:
        topo = load_topology(vdt_file)

    mismatches = pd.DataFrame()
    if topo is not None:
        mismatches = _check_topology_mismatches(flows, topo)
        if not mismatches.empty:
            logger.info(
                "Topology mismatches: %d flow rows not declared in .vdt",
                len(mismatches),
            )

    return TimesAnnualFlows(
        flows=flows,
        raw=raw,
        comnet=comnet,
        mappings_dir=mappings_dir,
        topology=topo,
        topology_mismatches=mismatches,
    )
