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
    # Netting is per export status: coloured mass is never cancelled against
    # untagged mass, and the untagged reverse flow is not promoted to `exported`.
    netted = net_collapsed_process_links(links)
    assert len(netted) == 2
    by_status = {str(r.export_status): r for r in netted.itertuples(index=False)}
    assert float(by_status["exported"].value) == 50.0
    assert by_status["exported"].source == "Power plants"
    assert float(by_status["context"].value) == 30.0
    assert by_status["context"].source == "Fuel Tech - Electricity (IND)"

    # Same status on both directions still nets to one ribbon.
    same = links.copy()
    same.loc[1, "export_status"] = "exported"
    same.loc[1, "exported"] = True
    netted_same = net_collapsed_process_links(same)
    assert len(netted_same) == 1
    assert float(netted_same.iloc[0]["value"]) == 20.0
    assert netted_same.iloc[0]["source"] == "Power plants"
    assert netted_same.iloc[0]["target"] == "Fuel Tech - Electricity (IND)"
    commodities = set(str(netted_same.iloc[0]["commodity"]).split("|"))
    assert commodities == {"HV", "Electricity (context)"}


def test_net_collapsed_leaves_imbalance_links():
    from times_pypsa.aggregation import net_collapsed_process_links

    links = pd.DataFrame(
        [
            {
                "source": "A",
                "target": "B",
                "source_kind": "process",
                "target_kind": "process",
                "value": 10.0,
                "export_status": "context",
                "exported": False,
                "commodity": "Elc",
                "matched_categories": "",
                "export_detail": "",
                "imbalance_class": "",
                "imbalance_tooltip": "",
            },
            {
                "source": "A",
                "target": "Unbalanced X",
                "source_kind": "process",
                "target_kind": "imbalance",
                "value": 2.0,
                "export_status": "context",
                "exported": False,
                "commodity": "Elc",
                "matched_categories": "",
                "export_detail": "",
                "imbalance_class": "unexplained",
                "imbalance_tooltip": "tip",
            },
        ]
    )
    netted = net_collapsed_process_links(links)
    assert len(netted) == 2
    imb = netted[netted["target_kind"] == "imbalance"]
    assert len(imb) == 1
    assert float(imb.iloc[0]["value"]) == 2.0


def test_collapse_cartesian_conserves_transferable_mass():
    """Multi-producer × multi-consumer collapse must conserve min(ΣFOut, ΣFIn)."""
    flows = pd.DataFrame(
        {
            "variable": ["VAR_FOUT", "VAR_FOUT", "VAR_FIN", "VAR_FIN"],
            "process_code": ["P1", "P2", "C1", "C2"],
            "commodity_code": ["ELC"] * 4,
            "commodity": ["Electricity"] * 4,
            "value": [6.0, 4.0, 3.0, 7.0],
            "exported": [True, False, True, False],
            "matched_categories": ["a", "", "b", ""],
        }
    )
    links = collapse_commodity_nodes(flows, flow_threshold=0.0)
    proc_links = links[
        (links["source_kind"] == "process") & (links["target_kind"] == "process")
    ]
    # transferable = min(10, 10) = 10; no residual
    assert abs(float(proc_links["value"].sum()) - 10.0) < 1e-9
    # No self-loops
    assert (proc_links["source"] != proc_links["target"]).all()
    # Proportional shares: P1→C1 = 6*(3/10)=1.8, P1→C2=4.2, P2→C1=1.2, P2→C2=2.8
    by_pair = {
        (r.source, r.target): float(r.value)
        for r in proc_links.itertuples(index=False)
    }
    assert abs(by_pair[("P1", "C1")] - 1.8) < 1e-9
    assert abs(by_pair[("P1", "C2")] - 4.2) < 1e-9
    assert abs(by_pair[("P2", "C1")] - 1.2) < 1e-9
    assert abs(by_pair[("P2", "C2")] - 2.8) < 1e-9


def test_aggregate_export_status_mixed_without_python_apply():
    """Vectorized export-status path: mixed when exported+context share a node pair."""
    flows = pd.DataFrame(
        {
            "year": [2050, 2050],
            "region": ["RW", "RW"],
            "variable": ["VAR_FIN", "VAR_FIN"],
            "process_code": ["P1", "P2"],
            "process": ["Proc1", "Proc2"],
            "commodity_code": ["ELC", "ELC"],
            "commodity": ["Electricity", "Electricity"],
            "value": [5.0, 3.0],
            "exported": [True, False],
            "matched_categories": ["cat", ""],
            "process_agg": ["Same", "Same"],
            "agg_level_2": ["Same", "Same"],
            "pypsa_carrier": ["Electricity", "Electricity"],
            "proc_agg__Aggregation Level 2": ["Same", "Same"],
            "com_agg__Aggregation Level 2": ["Electricity", "Electricity"],
            "sector": ["ELC", "ELC"],
            "process_type": ["PRE", "PRE"],
        }
    )
    agg = aggregate_flows(flows, level="Aggregation Level 2", apply_netting=True)
    assert len(agg) == 1
    assert agg.iloc[0]["export_status"] == "mixed"
    assert bool(agg.iloc[0]["exported"]) is False


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


def test_export_processes_are_a_separate_sankey_node():
    """Exports consume domestic energy: they must not sit in the import node."""
    from times_pypsa.aggregation import infer_overview_process_label

    assert (
        infer_overview_process_label(
            sector="SUP",
            process_type="IRE",
            description="Export Wood Pellets",
            agg_level_2="Export biomass",
            code="EXPBIOPEL",
        )
        == "Exports"
    )
    assert (
        infer_overview_process_label(
            sector="IND",
            process_type="PRE",
            description="Transfo_Exp",
            agg_level_2="Electricity Exports",
            code="Transfo_Exp",
        )
        == "Exports"
    )
    assert (
        infer_overview_process_label(
            sector="SUP",
            process_type="IRE",
            description="Import Natural Gas",
            agg_level_2="Imports",
            code="IMPGASNAT",
        )
        == "Imports & trade"
    )


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


def test_hydrogen_imports_friendly_rename_and_solar_fuel_tech_as_pv():
    """INDGH2C01_i displays as imported H2 delivery; RSDSOL00/COMSOL00 join PV."""
    from times_pypsa.aggregation import (
        _generation_split_label,
        friendly_custom_process_label,
        refine_custom_labels_for_readability,
    )

    assert friendly_custom_process_label("hydrogen imports") == "imported H2 delivery"
    assert (
        _generation_split_label(
            pd.Series(
                {
                    "process_code": "RSDSOL00",
                    "process": "Fuel Tech - Solar (RSD)",
                    "agg_level_2": "Solar",
                    "sector": "RSD",
                }
            )
        )
        == "PV"
    )

    df = pd.DataFrame(
        [
            {
                "process_code": "INDGH2C01_i",
                "process": "Fuel Tech - H2 Delivery from imported H2",
                "process_agg": "hydrogen imports",
                "agg_level_2": "hydrogen imports",
                "proc_agg__custom": "imported H2 delivery",
                "proc_agg__sankey_overview": "Fuel conversion",
                "com_agg__custom": "hydrogen for industry",
                "commodity_code": "INDHH2",
                "commodity": "hydrogen for industry",
                "pypsa_carrier": "hydrogen for industry",
                "exported": True,
            },
            {
                "process_code": "RSDSOL00",
                "process": "Fuel Tech - Solar (RSD)",
                "process_agg": "Solar",
                "agg_level_2": "Solar",
                "proc_agg__custom": "PV",
                "proc_agg__sankey_overview": "PV",
                "com_agg__custom": "Renewable: Solar",
                "commodity_code": "RENSOL",
                "commodity": "Renewable: Solar",
                "pypsa_carrier": "Renewable: Solar",
                "exported": False,
                "sector": "RSD",
            },
        ]
    )
    proc_out, _ = refine_custom_labels_for_readability(
        df["proc_agg__custom"], df["com_agg__custom"], df
    )
    assert proc_out.iloc[0] == "imported H2 delivery"
    assert proc_out.iloc[1] == "PV"


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


def test_commercial_other_and_buildings_custom_splits():
    """Sankey splits services end-uses and residential vs commercial buildings."""
    from times_pypsa.aggregation import refine_custom_labels_for_readability

    rows = [
        {
            "process_code": "CCCOELC100",
            "process": "Com space cool",
            "process_agg": "commercial other",
            "agg_level_2": "commercial other",
            "proc_agg__custom": "Commercial cooling",
            "proc_agg__sankey_overview": "Buildings",
            "com_agg__custom": "Electricity",
            "commodity_code": "COMELC",
            "commodity": "Electricity",
            "pypsa_carrier": "Electricity",
            "exported": True,
            "sector": "COM",
        },
        {
            "process_code": "CLIGCOELC501",
            "process": "Com lighting",
            "process_agg": "commercial other",
            "agg_level_2": "commercial other",
            "proc_agg__custom": "Commercial lighting",
            "proc_agg__sankey_overview": "Buildings",
            "com_agg__custom": "Electricity",
            "commodity_code": "COMELC",
            "commodity": "Electricity",
            "pypsa_carrier": "Electricity",
            "exported": True,
            "sector": "COM",
        },
        {
            "process_code": "COM_CBAT_CO",
            "process": "Building Existing CO",
            "process_agg": "Building Existing CO",
            "agg_level_2": "Building Existing CO",
            "proc_agg__custom": "Commercial buildings",
            "proc_agg__sankey_overview": "Buildings",
            "com_agg__custom": "Commercial cooling",
            "commodity_code": "CCCO",
            "commodity": "cooling",
            "pypsa_carrier": "Commercial cooling",
            "exported": False,
            "sector": "COM",
        },
        {
            "process_code": "RDW_R_4Fac",
            "process": "Existing Building_4 façades",
            "process_agg": "Buildings: built area",
            "agg_level_2": "Buildings: built area",
            "proc_agg__custom": "Residential buildings",
            "proc_agg__sankey_overview": "Buildings",
            "com_agg__custom": "Heat",
            "commodity_code": "RH4F",
            "commodity": "heat",
            "pypsa_carrier": "Heat",
            "exported": False,
            "sector": "RSD",
        },
    ]
    df = pd.DataFrame(rows)
    proc_out, _ = refine_custom_labels_for_readability(
        df["proc_agg__custom"], df["com_agg__custom"], df
    )
    assert proc_out.tolist() == [
        "Commercial cooling",
        "Commercial lighting",
        "Commercial buildings",
        "Residential buildings",
    ]


def test_refine_keeps_transport_mode_labels_not_other():
    """Vehicle DEM is context (fuel techs are exported); keep Cars/Freight/buses."""
    df = pd.DataFrame(
        {
            "year": [2030] * 4,
            "region": ["RW"] * 4,
            "variable": ["VAR_FIN"] * 4,
            "process_code": ["TCAR1", "THDT1", "TBUS1", "TMOP1"],
            "process": ["Car gasoline", "Truck diesel", "Bus diesel", "Moped"],
            "commodity_code": ["TRAGSL", "TRADST", "TRADST", "TRAGSL"],
            "commodity": ["Gasoline"] * 4,
            "value": [1.0, 1.0, 1.0, 1.0],
            "exported": [False, False, False, False],
            "matched_categories": [""] * 4,
            "process_agg": ["Cars", "Road Freight", "Road transport (public)", "2 and 3 wheelers"],
            "agg_level_2": ["Cars", "Road Freight", "Road transport (public)", "2 and 3 wheelers"],
            "sector": ["TRA"] * 4,
            "process_type": ["DMD"] * 4,
            "pypsa_carrier": [""] * 4,
            "proc_agg__custom": [
                "Cars",
                "Road Freight",
                "Road transport (public)",
                "2 and 3 wheelers",
            ],
            "com_agg__custom": ["Oil products"] * 4,
            "proc_agg__sankey_overview": ["Transport"] * 4,
            "com_agg__sankey_overview": ["Oil products"] * 4,
        }
    )
    # One exported fuel-tech row so refine runs in export mode.
    fuel = df.iloc[[0]].copy()
    fuel["process_code"] = "TRADST00"
    fuel["process"] = "Fuel Tech Diesel TRA"
    fuel["exported"] = True
    fuel["matched_categories"] = "total road"
    fuel["process_agg"] = "Fuel Tech - Diesel (TRA)"
    fuel["agg_level_2"] = "Fuel Tech - Diesel (TRA)"
    fuel["proc_agg__custom"] = "Fuel Tech - Diesel (TRA)"
    fuel["proc_agg__sankey_overview"] = "Fuel conversion"
    df = pd.concat([df, fuel], ignore_index=True)

    proc_out, _ = refine_custom_labels_for_readability(
        df["proc_agg__custom"], df["com_agg__custom"], df
    )
    assert proc_out.iloc[0] == "Cars"
    assert proc_out.iloc[1] == "Road Freight"
    assert proc_out.iloc[2] == "Road transport (public)"
    assert proc_out.iloc[3] == "2 and 3 wheelers"
    assert not any(lab == "Transport (other)" for lab in proc_out.tolist())
