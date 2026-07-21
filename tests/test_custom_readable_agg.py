"""Readable `custom` aggregation: keep export L2, collapse context."""

from __future__ import annotations

import pandas as pd

from times_pypsa.aggregation import (
    aggregate_flows,
    collapse_commodity_nodes,
    refine_custom_labels_for_readability,
)


def _flows() -> pd.DataFrame:
    """Exported heat pump + context power plant sharing electricity carrier family."""
    return pd.DataFrame(
        {
            "year": [2050] * 6,
            "region": ["RW"] * 6,
            "variable": ["VAR_FOUT", "VAR_FIN", "VAR_FOUT", "VAR_FIN", "VAR_FOUT", "VAR_FIN"],
            "process_code": ["HP1", "HP1", "GEN1", "FT_ELC", "IMP1", "FT_ELC"],
            "process": ["Heat pump", "Heat pump", "Gas turbine", "Fuel tech ELC", "Import", "Fuel tech ELC"],
            "commodity_code": ["RH2F", "RSDELC", "RSDELC", "RSDELC", "OILDST", "OILDST"],
            "commodity": [
                "space heating 2f",
                "Electricity for Residential sector",
                "Electricity for Residential sector",
                "Electricity for Residential sector",
                "Diesel",
                "Diesel",
            ],
            "value": [8.0, 3.0, 5.0, 3.0, 4.0, 4.0],
            "exported": [True, True, False, False, False, True],
            "matched_categories": ["heat pump", "residential elc", "", "", "", "total road"],
            "process_agg": [
                "Residential urban decentral Heat pump",
                "Residential urban decentral Heat pump",
                "Gas power plants",
                "Fuel Tech - Electricity",
                "Imports",
                "Fuel Tech - Diesel (TRA)",
            ],
            "agg_level_2": [
                "Residential urban decentral Heat pump",
                "Residential urban decentral Heat pump",
                "Gas power plants",
                "Fuel Tech - Electricity",
                "Imports",
                "Fuel Tech - Diesel (TRA)",
            ],
            "sector": ["RSD", "RSD", "ELC", "ELC", "IMP", "TRA"],
            "process_type": ["PRE", "PRE", "ELE", "PRE", "IMP", "PRE"],
            "pypsa_carrier": ["Heat", "Electricity", "Electricity", "Electricity", "Diesel", "Diesel"],
            "proc_agg__custom": [
                "Residential urban decentral Heat pump",
                "Residential urban decentral Heat pump",
                "Power plants",
                "Fuel Tech - Electricity",
                "Imports & trade",
                "Fuel Tech - Diesel (TRA)",
            ],
            "com_agg__custom": [
                "Heat",
                "Electricity",
                "Electricity",
                "Electricity",
                "Oil products",
                "Diesel",
            ],
            "proc_agg__sankey_overview": [
                "Buildings",
                "Buildings",
                "Power plants",
                "Fuel conversion",
                "Imports & trade",
                "Fuel conversion",
            ],
            "com_agg__sankey_overview": [
                "Heat",
                "Electricity",
                "Electricity",
                "Electricity",
                "Oil products",
                "Oil products",
            ],
        }
    )


def test_refine_keeps_export_l2_and_collapses_context():
    df = _flows()
    proc = df["proc_agg__custom"]
    com = df["com_agg__custom"]
    proc_out, com_out = refine_custom_labels_for_readability(proc, com, df)

    # Export-touching heat-pump L2 kept; PV collapsed to overview power.
    assert proc_out.iloc[0] == "Residential urban decentral Heat pump"
    assert proc_out.iloc[2] == "Power plants"
    assert proc_out.iloc[4] == "Imports & trade"
    assert proc_out.iloc[5] == "Fuel Tech - Diesel (TRA)"

    # RSDELC is export-touching → same specific label on all its rows.
    assert com_out.iloc[1] == com_out.iloc[2] == com_out.iloc[3]
    assert "Electricity" in com_out.iloc[1]
    assert com_out.iloc[1] != "Electricity"  # not bare family
    # Pure context oil family (no — OILDST is export-touching via diesel row)
    assert com_out.iloc[5] == "Diesel"


def test_custom_aggregate_node_budget_and_export_links():
    df = _flows()
    agg = aggregate_flows(df, level="custom", apply_netting=False)
    # Far fewer process nodes than raw codes (6 → a handful of role/L2 labels)
    assert agg["process_code"].nunique() <= 5
    links = collapse_commodity_nodes(agg, flow_threshold=0.0)
    assert not links.empty
    exported_pj = float(
        links.loc[links["export_status"].isin(["exported", "double_count"]), "value"].sum()
    )
    # Heat-pump heat out and diesel fuel-tech should remain visible as exported.
    assert exported_pj >= 8.0


def test_generation_split_labels_pv_and_wind():
    from times_pypsa.aggregation import _generation_split_label

    assert (
        _generation_split_label(
            pd.Series(
                {
                    "process_code": "ERNW_PV-Buildings_SOL_N",
                    "process": "Solar PV Buildings",
                    "agg_level_2": "PV",
                    "sector": "ELC",
                }
            )
        )
        == "PV"
    )
    assert (
        _generation_split_label(
            pd.Series(
                {
                    "process_code": "ERNW_WINON_WIN_N",
                    "process": "Wind Onshore Large",
                    "agg_level_2": "Renewables",
                    "sector": "ELC",
                }
            )
        )
        == "Onshore wind"
    )
    # Offshore wind is outside Wallonia — stay under Imports & trade (no split).
    assert (
        _generation_split_label(
            pd.Series(
                {
                    "process_code": "IMPELCOFFWINBE",
                    "process": "Electricity import - off wind",
                    "agg_level_2": "Electricity Imports",
                    "sector": "SUP",
                }
            )
        )
        == ""
    )


def test_carrier_families_do_not_mix_gas_and_electricity():
    from times_pypsa.aggregation import infer_carrier_family, infer_overview_commodity_label

    assert (
        infer_overview_commodity_label(
            description="Natural Gas for Electricity",
            code="ELCGAS",
            carrier="Natural Gas for Electricity",
        )
        == "Gas"
    )
    assert (
        infer_carrier_family(
            description="Uranium (Dummy Reserves)",
            code="NUCRSV",
            carrier="Uranium (Dummy Reserves)",
        )
        == "Nuclear fuel"
    )
    assert (
        infer_carrier_family(
            description="HV electricity",
            code="ELCHIG",
            carrier="Electricity",
        )
        == "Electricity"
    )
    assert infer_carrier_family(
        description="Natural Gas for Electricity",
        code="ELCGAS",
        carrier="Natural Gas for Electricity",
    ) != infer_carrier_family(
        description="HV electricity",
        code="ELCHIG",
        carrier="Electricity",
    )


def test_net_collapsed_process_links_nets_reciprocal():
    from times_pypsa.aggregation import net_collapsed_process_links

    links = pd.DataFrame(
        [
            {
                "source": "Power plants",
                "target": "Fuel Tech - Electricity (IND)",
                "source_kind": "process",
                "target_kind": "process",
                "value": 50.0,
                "export_status": "exported",
                "exported": True,
                "commodity": "HV",
                "matched_categories": "",
                "export_detail": "",
                "imbalance_class": "",
                "imbalance_tooltip": "",
            },
            {
                "source": "Fuel Tech - Electricity (IND)",
                "target": "Power plants",
                "source_kind": "process",
                "target_kind": "process",
                "value": 30.0,
                "export_status": "context",
                "exported": False,
                "commodity": "Electricity (context)",
                "matched_categories": "",
                "export_detail": "",
                "imbalance_class": "",
                "imbalance_tooltip": "",
            },
        ]
    )
    netted = net_collapsed_process_links(links)
    assert len(netted) == 1
    assert float(netted.iloc[0]["value"]) == 20.0
    assert netted.iloc[0]["source"] == "Power plants"
    assert netted.iloc[0]["target"] == "Fuel Tech - Electricity (IND)"


def test_chp_and_district_heating_are_separate():
    from times_pypsa.aggregation import infer_overview_process_label

    assert (
        infer_overview_process_label(
            sector="ELC",
            process_type="CHP",
            description="New Public - CCGT advanced CHP",
            agg_level_2="CHP",
            code="ECHPP_CCGT-CHP_GAS_N",
        )
        == "CHP"
    )
    assert (
        infer_overview_process_label(
            sector="RSD",
            process_type="PRE",
            description="District heat exchanger.HeatHotwater-RH2F-New1",
            agg_level_2="District heating",
            code="RH2FHETN1",
        )
        == "District heating"
    )


def test_heat_pumps_not_classified_as_chp():
    """Codes like *ELCHP* must not be treated as combined heat & power."""
    from times_pypsa.aggregation import infer_overview_process_label

    assert (
        infer_overview_process_label(
            sector="COM",
            process_type="PRE",
            description="Com Space Heat New heat pump - CS - ELC401",
            agg_level_2="Commercial Heat pump",
            code="CHCSELCHP401",
        )
        == "Buildings"
    )
    assert (
        infer_overview_process_label(
            sector="RSD",
            process_type="PRE",
            description="Air heat pump with electric boiler-RH2F-New4",
            agg_level_2="Residential urban decentral Heat pump",
            code="RH2FELCHPN4",
        )
        == "Buildings"
    )


def test_specific_custom_not_overridden_by_overview_chp():
    """Wrong sankey_overview=CHP must not steal a specific custom heat-pump label."""
    from times_pypsa.aggregation import refine_custom_labels_for_readability

    df = pd.DataFrame(
        [
            {
                "process_code": "CHCSELCHP401",
                "process": "Com Space Heat New heat pump",
                "process_agg": "Commercial Heat pump",
                "agg_level_2": "Commercial Heat pump",
                "proc_agg__custom": "Commercial Heat pump",
                "proc_agg__sankey_overview": "CHP",
                "com_agg__custom": "Heat",
                "commodity_code": "CHCS",
                "commodity": "Space heating",
                "pypsa_carrier": "Heat",
                "exported": True,
            }
        ]
    )
    proc_out, _ = refine_custom_labels_for_readability(
        df["proc_agg__custom"], df["com_agg__custom"], df
    )
    assert proc_out.iloc[0] == "Commercial Heat pump"


def test_end_use_fuel_tech_splits_by_rule_commodity():
    from times_pypsa.aggregation import end_use_fuel_tech_label, rule_commodity_for_fuel_tech

    assert rule_commodity_for_fuel_tech(process_agg="Fuel Tech - Electricity (IND)") == "electricity"
    assert (
        rule_commodity_for_fuel_tech(process_agg="Fuel Tech - Natural Gas transport (IND)")
        == "methane"
    )
    assert end_use_fuel_tech_label(process_agg="Fuel Tech - Coke (IND)") == "Fuel tech · coke"


def test_dum_retrofit_not_imports():
    from times_pypsa.aggregation import infer_overview_process_label

    assert (
        infer_overview_process_label(
            sector="RSD",
            process_type="IRE",
            description="Dum_Retrofit",
            agg_level_2="Dum_Retrofit",
            code="Dum_Retrofit",
        )
        == "Building retrofits"
    )


def test_energy_filter_keeps_retrofit_heat_fout():
    """Soft-linking is universal: retrofit useful-heat FOut must not be dropped."""
    from times_pypsa.qa import filter_energy_carrier_flows

    df = pd.DataFrame(
        [
            {
                "commodity_code": "RH2F",
                "pypsa_carrier": "Heat",
                "process_agg": "Retrofitting improvements",
                "process_code": "Retrofit-T_R_2Fac1945",
                "value": 1.0,
            },
            {
                "commodity_code": "ELCCO2",
                "pypsa_carrier": "CO2",
                "process_agg": "Power plants",
                "process_code": "ETSTP_X",
                "value": 9.0,
            },
            {
                "commodity_code": "Dum-Retrofit-X",
                "pypsa_carrier": "",
                "process_agg": "Dum_Retrofit",
                "process_code": "Dum_Retrofit",
                "value": 5.0,
            },
        ]
    )
    kept = filter_energy_carrier_flows(df)
    assert len(kept) == 1
    assert kept.iloc[0]["process_code"] == "Retrofit-T_R_2Fac1945"
