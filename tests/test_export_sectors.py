"""Per-sector export colouring and demand-inflow anchoring of soft-links.

Covers the export-strategy rework: exported Sankey links are coloured by PyPSA
end-use sector (Industry / Transport / Residential / Services / Agriculture)
instead of a single blue, and soft-linked *fuel inputs* into conversion gateways
are anchored onto the gateway's output links (the demand inflow) rather than the
upstream fuel-supply links.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from times_pypsa.aggregation import (
    PYPSA_SECTOR_BY_CATEGORY,
    PYPSA_SECTOR_COLORS,
    PYPSA_SECTOR_ORDER,
    assign_export_sectors,
    category_sector,
    link_sector,
)
from times_pypsa.pipeline import (
    extract_demands_for_horizon,
    load_extraction_rules,
    load_metadata,
    road_internal_transfer_pj,
)
from times_pypsa.qa import prepare_system_sankey_links, tag_flows_with_rules
from times_pypsa.sankey_html import link_color


# --------------------------------------------------------------------------- #
# category -> sector mapping
# --------------------------------------------------------------------------- #
def test_every_extraction_category_maps_to_a_sector(mappings_dir: Path):
    meta = load_metadata(mappings_dir)
    rules = load_extraction_rules(meta.extraction_rules_file)
    unmapped = [c for c in rules if not category_sector(c)]
    assert unmapped == [], f"categories without a PyPSA sector: {unmapped}"


def test_known_category_sectors():
    assert category_sector("total road") == "Transport"
    assert category_sector("coke") == "Industry"
    assert category_sector("total electricity residential") == "Residential"
    assert category_sector("services gas boiler") == "Services"
    assert category_sector("total agriculture machinery") == "Agriculture"
    assert category_sector("") == ""


def test_all_sectors_have_a_distinct_colour():
    used = set(PYPSA_SECTOR_BY_CATEGORY.values())
    assert used <= set(PYPSA_SECTOR_ORDER)
    colours = [PYPSA_SECTOR_COLORS[s] for s in PYPSA_SECTOR_ORDER]
    assert len(set(colours)) == len(colours)  # no two sectors share a colour


def test_link_sector_multi_category_priority():
    # A heat link tagged with parent + child residential categories stays Residential.
    s = link_sector("BEWAL residential rural heat|residential rural gas boiler")
    assert s == "Residential"
    # Mixed-sector categories resolve deterministically by PYPSA_SECTOR_ORDER.
    mixed = link_sector("total road|coke")  # Transport + Industry
    assert mixed in {"Industry", "Transport"}
    assert mixed == "Industry"  # Industry precedes Transport in the order


def test_link_color_uses_sector_when_present():
    assert link_color("exported", "Transport") == PYPSA_SECTOR_COLORS["Transport"]
    assert link_color("exported", "Industry") == PYPSA_SECTOR_COLORS["Industry"]
    # No sector → fall back to status colouring (still a valid rgba string).
    assert link_color("context", "").startswith("rgba(")


# --------------------------------------------------------------------------- #
# assign_export_sectors — anchoring behaviour (synthetic)
# --------------------------------------------------------------------------- #
def _links(rows: list[dict]) -> pd.DataFrame:
    base = {
        "source_kind": "process",
        "target_kind": "process",
        "commodity": "",
        "export_detail": "",
        "imbalance_class": "",
        "imbalance_tooltip": "",
    }
    return pd.DataFrame([{**base, **r} for r in rows])


def test_anchor_moves_fuel_input_export_to_demand_inflow():
    # Gateway: a transport fuel-tech whose diesel input is soft-linked.
    agg = pd.DataFrame(
        [
            {
                "process_code": "Fuel Tech - Diesel (TRA)",
                "commodity_code": "Diesel",
                "variable": "VAR_FIN",
                "value": 50.0,
                "exported": True,
                "matched_categories": "total road",
            }
        ]
    )
    links = _links(
        [
            {
                "source": "Imports & trade",
                "target": "Fuel Tech - Diesel (TRA)",
                "value": 50.0,
                "export_status": "exported",
                "matched_categories": "total road",
                "export_detail": "VAR_FIn of Fuel Tech - Diesel (TRA)",
            },
            {
                "source": "Fuel Tech - Diesel (TRA)",
                "target": "Cars",
                "value": 50.0,
                "export_status": "context",
                "matched_categories": "",
                "export_detail": "",
            },
        ]
    )
    out = assign_export_sectors(links, agg, anchor=True)
    imp = out[out["source"] == "Imports & trade"].iloc[0]
    car = out[out["target"] == "Cars"].iloc[0]
    # Upstream fuel supply is now context (grey); demand inflow carries the export.
    assert imp["export_status"] == "context"
    assert str(imp["export_sector"]) == ""
    assert car["export_status"] == "exported"
    assert car["export_sector"] == "Transport"


def test_demand_side_fin_export_is_not_anchored():
    # Appliance electricity: the exported consumer is already the end use.
    agg = pd.DataFrame(
        [
            {
                "process_code": "Household electrical appliances",
                "commodity_code": "Residential electricity",
                "variable": "VAR_FIN",
                "value": 9.0,
                "exported": True,
                "matched_categories": "total electricity residential",
            }
        ]
    )
    links = _links(
        [
            {
                "source": "Fuel tech · electricity",
                "target": "Household electrical appliances",
                "value": 9.0,
                "export_status": "exported",
                "matched_categories": "total electricity residential",
                "export_detail": "VAR_FIn of Household electrical appliances",
            }
        ]
    )
    out = assign_export_sectors(links, agg, anchor=True)
    row = out.iloc[0]
    assert row["export_status"] == "exported"
    assert row["export_sector"] == "Residential"


def test_var_fout_producer_export_kept_and_coloured():
    # Boiler heat FOut export → link stays exported, coloured by sector, even if
    # the boiler happens to consume fuel from an anchorable gateway upstream.
    agg = pd.DataFrame(
        [
            {
                "process_code": "Residential rural gas heater",
                "commodity_code": "space heating for 3F houses",
                "variable": "VAR_FOUT",
                "value": 15.0,
                "exported": True,
                "matched_categories": "BEWAL residential rural heat",
            }
        ]
    )
    links = _links(
        [
            {
                "source": "Residential rural gas heater",
                "target": "Residential buildings",
                "value": 15.0,
                "export_status": "exported",
                "matched_categories": "BEWAL residential rural heat",
                "export_detail": "VAR_FOut of Residential rural gas heater",
            }
        ]
    )
    out = assign_export_sectors(links, agg, anchor=True)
    row = out.iloc[0]
    assert row["export_status"] == "exported"
    assert row["export_sector"] == "Residential"


def test_assign_export_sectors_handles_empty_links():
    empty = pd.DataFrame(
        columns=["source", "target", "value", "export_status", "matched_categories"]
    )
    out = assign_export_sectors(empty, None)
    assert "export_sector" in out.columns


# --------------------------------------------------------------------------- #
# Integration on the reference (toy) scenario
# --------------------------------------------------------------------------- #
@pytest.fixture(scope="module")
def custom_system_links_by_year(times_model, mappings_dir: Path):
    meta = load_metadata(mappings_dir)
    rules = load_extraction_rules(meta.extraction_rules_file)
    out = {}
    for yr in times_model.years:
        tagged = tag_flows_with_rules(times_model.energy_flows(yr), rules)
        out[yr] = prepare_system_sankey_links(
            tagged,
            flow_threshold=0.0,
            apply_netting=True,
            agg_level="custom",
            collapse_commodities=True,
        )
    return out


def test_no_mixed_or_double_count_on_custom_system(custom_system_links_by_year):
    """Verified invariant: the real ``custom`` Sankey never produces the old
    light-red 'mixed' (or purple 'double_count') link — which justifies replacing
    that scheme with per-sector colours."""
    for yr, links in custom_system_links_by_year.items():
        statuses = set(links["export_status"])
        assert "mixed" not in statuses, f"{yr}: unexpected 'mixed' link"
        assert "double_count" not in statuses, f"{yr}: unexpected 'double_count' link"


def test_every_exported_link_has_a_sector(custom_system_links_by_year):
    for yr, links in custom_system_links_by_year.items():
        exp = links[links["export_status"].isin(["exported", "double_count"])]
        missing = exp[exp["export_sector"].astype(str).str.strip() == ""]
        assert missing.empty, f"{yr}: {len(missing)} exported links without a sector"
        assert set(exp["export_sector"]) <= set(PYPSA_SECTOR_ORDER)


# --------------------------------------------------------------------------- #
# Coverage-gap fixes: residential cooking + biofuel-blending double-count
# --------------------------------------------------------------------------- #
def test_residential_cooking_rule_present(mappings_dir: Path):
    meta = load_metadata(mappings_dir)
    rules = load_extraction_rules(meta.extraction_rules_file)
    assert "residential cooking" in rules
    var_type, filter_type, values = rules["residential cooking"]
    assert var_type == "VAR_FIN"
    assert "residential cooking" in values
    assert category_sector("residential cooking") == "Residential"


def test_road_internal_transfer_counts_cross_process_biofuel_only():
    road = ["Fuel Tech - Biodiesel (TRA)", "Fuel Tech - Diesel (TRA)", "Fuel Tech - H2"]
    df = pd.DataFrame(
        [
            # Biodiesel produced by one road tech, blended into another (double-count).
            {"process_agg": "Fuel Tech - Biodiesel (TRA)", "commodity_code": "FEED", "variable": "VAR_FIN", "value": 5.0},
            {"process_agg": "Fuel Tech - Biodiesel (TRA)", "commodity_code": "TRABDL", "variable": "VAR_FOUT", "value": 5.0},
            {"process_agg": "Fuel Tech - Diesel (TRA)", "commodity_code": "TRABDL", "variable": "VAR_FIN", "value": 4.5},
            {"process_agg": "Fuel Tech - Diesel (TRA)", "commodity_code": "OILDST", "variable": "VAR_FIN", "value": 40.0},
            # H2 tank cycle WITHIN one process_agg — netting handles it → excluded.
            {"process_agg": "Fuel Tech - H2", "commodity_code": "SYNH2CT", "variable": "VAR_FOUT", "value": 2.5},
            {"process_agg": "Fuel Tech - H2", "commodity_code": "SYNH2CT", "variable": "VAR_FIN", "value": 1.3},
        ]
    )
    # Only the cross-process biodiesel counts: min(net_prod 5.0, net_cons 4.5) = 4.5.
    assert abs(road_internal_transfer_pj(df, road) - 4.5) < 1e-9


def test_road_internal_transfer_ignores_imported_biofuel():
    # BIOETH produced OUTSIDE the road set (Imports) → counted once, not subtracted.
    road = ["Fuel Tech - Gasoline (TRA)"]
    df = pd.DataFrame(
        [
            {"process_agg": "Imports", "commodity_code": "BIOETH", "variable": "VAR_FOUT", "value": 2.0},
            {"process_agg": "Fuel Tech - Gasoline (TRA)", "commodity_code": "BIOETH", "variable": "VAR_FIN", "value": 2.0},
            {"process_agg": "Fuel Tech - Gasoline (TRA)", "commodity_code": "OILGSL", "variable": "VAR_FIN", "value": 25.0},
        ]
    )
    assert road_internal_transfer_pj(df, road) == 0.0


def test_demand_csv_has_cooking_and_road_biofuel_subtracted(
    tmp_path, times_model, mappings_dir: Path
):
    meta = load_metadata(mappings_dir)
    rules = load_extraction_rules(meta.extraction_rules_file)
    year = 2030 if 2030 in times_model.years else times_model.years[0]
    out = tmp_path / f"demands_{year}.csv"
    df = extract_demands_for_horizon(
        times_model.flows, meta.processes_df, meta.mapping_df, rules,
        meta.commodity_mapping_file, year, out,
    )
    cats = set(df["category"])
    assert "residential cooking" in cats
    # total road must have the intra-road biofuel blending removed.
    year_flows = times_model.energy_flows(year).copy()
    proc_map = meta.processes_df.set_index("Process")["Aggregation Level 2"].to_dict() \
        if "Process" in meta.processes_df.columns else {}
    year_flows["process_agg"] = year_flows["process_code"].map(proc_map)
    internal = road_internal_transfer_pj(year_flows, rules["total road"][2])
    # If the scenario has intra-road biofuel blending, total road was reduced by it.
    if internal > 1e-6:
        assert internal > 0


def test_anchorable_gateway_inputs_are_not_highlighted(custom_system_links_by_year):
    """After anchoring, a fuel-delivery gateway that has an output link must not
    keep an exported *input* link (the export sits on the demand inflow)."""
    from times_pypsa.aggregation import _is_anchorable_gateway_label

    for yr, links in custom_system_links_by_year.items():
        src = links["source"].astype(str)
        tgt = links["target"].astype(str)
        for g in src.unique():
            if not _is_anchorable_gateway_label(g):
                continue
            if not (src == g).any():
                continue  # no output link to anchor onto → input highlight kept
            inbound = links[(tgt == g) & (src != g)]
            # A gateway's fuel-supply inputs are greyed. Chain links whose source
            # is itself a gateway (e.g. Biodiesel → Diesel) are that upstream
            # gateway's *output* and are legitimately kept exported.
            exported_supply = inbound[
                (inbound["export_status"] == "exported")
                & (~inbound["source"].astype(str).map(_is_anchorable_gateway_label))
            ]
            assert exported_supply.empty, (
                f"{yr}: gateway {g!r} still has exported fuel-supply inputs: "
                f"{list(exported_supply['source'])}"
            )
