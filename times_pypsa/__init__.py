"""TIMES → PyPSA soft-linking: demand extraction and Sankey diagrams."""

from times_pypsa.aggregation import (
    PYPSA_SECTOR_COLORS,
    PYPSA_SECTOR_ORDER,
    aggregate_flows,
    assign_export_sectors,
    available_agg_levels,
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
from times_pypsa.heat_softlink import (
    default_groups_file,
    extract_heating_capacities,
    extract_heating_targets,
    heat_group_targets,
    heating_capacities,
    load_heat_groups,
    resolve_groups_file,
)
from times_pypsa.transport_softlink import (
    extract_road_transport,
    load_transport_groups,
    road_transport_shares,
    road_vehicle_fleet,
)
from times_pypsa.named_transfers import (
    extract_industrial_capture,
    extract_pv_rooftop_share,
    industrial_capture_kt,
    pv_rooftop_share,
)
from times_pypsa.qa import generate_qa_report
from times_pypsa.sankey_pages import (
    DEFAULT_PAGE_LEVELS,
    DEFAULT_INDEX_NAME,
    export_sankey_pages,
    level_slug,
    sankey_page_name,
    sankey_page_names,
)
from times_pypsa.model import TimesAnnualFlows, load_times_annual_flows
from times_pypsa.topology import Topology, load_topology

__all__ = [
    "DEFAULT_INDEX_NAME",
    "DEFAULT_PAGE_LEVELS",
    "PYPSA_SECTOR_COLORS",
    "PYPSA_SECTOR_ORDER",
    "PipelineConfig",
    "TimesAnnualFlows",
    "Topology",
    "aggregate_flows",
    "assign_export_sectors",
    "available_agg_levels",
    "build_sankey_label_map",
    "category_sector",
    "collapse_commodity_nodes",
    "com_agg_col",
    "link_sector",
    "default_groups_file",
    "default_mappings_dir",
    "export_all_horizons",
    "export_coupling_dir",
    "export_horizon",
    "export_sankey_pages",
    "extract_heating_capacities",
    "extract_heating_targets",
    "extract_industrial_capture",
    "extract_pv_rooftop_share",
    "extract_road_transport",
    "industrial_capture_kt",
    "pv_rooftop_share",
    "generate_qa_report",
    "generate_sankey",
    "heat_group_targets",
    "heating_capacities",
    "level_slug",
    "load_heat_groups",
    "load_times_annual_flows",
    "load_transport_groups",
    "load_topology",
    "proc_agg_col",
    "road_transport_shares",
    "road_vehicle_fleet",
    "resolve_groups_file",
    "sankey_page_name",
    "sankey_page_names",
    "shared_aggregation_columns",
]
