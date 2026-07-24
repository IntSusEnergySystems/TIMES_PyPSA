"""TIMES → PyPSA soft-linking: demand extraction and Sankey diagrams."""

from times_pypsa.aggregation import (
    PYPSA_SECTOR_COLORS,
    PYPSA_SECTOR_ORDER,
    aggregate_flows,
    assign_export_sectors,
    category_sector,
    com_agg_col,
    link_sector,
    proc_agg_col,
    shared_aggregation_columns,
    build_sankey_label_map,
    collapse_commodity_nodes,
)
from times_pypsa.pipeline import (
    PipelineConfig,
    default_mappings_dir,
    export_all_horizons,
    export_coupling_dir,
    export_horizon,
    generate_sankey,
)
from times_pypsa.qa import generate_qa_report
from times_pypsa.model import TimesAnnualFlows, load_times_annual_flows
from times_pypsa.topology import Topology, load_topology

__all__ = [
    "PYPSA_SECTOR_COLORS",
    "PYPSA_SECTOR_ORDER",
    "PipelineConfig",
    "TimesAnnualFlows",
    "Topology",
    "aggregate_flows",
    "assign_export_sectors",
    "build_sankey_label_map",
    "category_sector",
    "collapse_commodity_nodes",
    "com_agg_col",
    "link_sector",
    "default_mappings_dir",
    "export_all_horizons",
    "export_coupling_dir",
    "export_horizon",
    "generate_qa_report",
    "generate_sankey",
    "load_times_annual_flows",
    "load_topology",
    "proc_agg_col",
    "shared_aggregation_columns",
]
