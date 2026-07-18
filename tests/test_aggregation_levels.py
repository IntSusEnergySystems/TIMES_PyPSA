"""Fast unit tests for column-based Sankey aggregation levels."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from times_pypsa.aggregation import (
    aggregate_flows,
    com_agg_col,
    proc_agg_col,
    sankey_links_from_flows,
    shared_aggregation_columns,
)


def _toy_flows(**extra: object) -> pd.DataFrame:
    base = {
        "year": [2020, 2020, 2020, 2020],
        "region": ["RW", "RW", "RW", "RW"],
        "variable": ["VAR_FIN", "VAR_FOUT", "VAR_FIN", "VAR_FOUT"],
        "process_code": ["P1", "P1", "P2", "P2"],
        "process": ["Proc1", "Proc1", "Proc2", "Proc2"],
        "commodity_code": ["C1", "C1", "C2", "C2"],
        "commodity": ["Com1", "Com1", "Com2", "Com2"],
        "value": [10.0, 10.0, 5.0, 5.0],
    }
    base.update(extra)
    return pd.DataFrame(base)


def test_shared_aggregation_columns_intersection_minus_excluded():
    processes_df = pd.DataFrame(
        {
            "Process": ["P1"],
            "Sector": ["ELC"],
            "Description": ["process desc"],
            "Type": ["PRE"],
            "sankey_overview": ["Power"],
        }
    )
    commodities_df = pd.DataFrame(
        {
            "TIMES commodity": ["C1"],
            "Sector": ["ELC"],
            "Description": ["commodity desc"],
            "Type": ["NRG"],
            "sankey_overview": ["Power"],
        }
    )

    cols = shared_aggregation_columns(processes_df, commodities_df)

    assert "Sector" in cols
    assert "sankey_overview" in cols
    assert "Process" not in cols
    assert "TIMES commodity" not in cols
    assert "Description" not in cols
    assert "Type" not in cols


def test_proc_and_com_agg_col_helpers():
    assert proc_agg_col("Sector") == "proc_agg__Sector"
    assert com_agg_col("sankey_overview") == "com_agg__sankey_overview"


def test_aggregate_flows_sankey_overview_column_labels():
    flows = _toy_flows(
        proc_agg__sankey_overview=["GroupA", "GroupA", "GroupA", "GroupB"],
        com_agg__sankey_overview=["CarrierX", "CarrierX", "CarrierY", "CarrierY"],
    )

    agg = aggregate_flows(flows, level="sankey_overview", apply_netting=False)

    assert set(agg["process_code"]) == {"GroupA", "GroupB"}
    assert set(agg["commodity_code"]) == {"CarrierX", "CarrierY"}


def test_empty_agg_labels_never_become_unknown():
    flows = _toy_flows(
        proc_agg__sankey_overview=["", "", "", ""],
        com_agg__sankey_overview=["", "", "", ""],
        process=["Gas boiler", "Gas boiler", "Wind farm", "Wind farm"],
        commodity=["Network gas", "Network gas", "Electricity HV", "Electricity HV"],
        sector=["RSD", "RSD", "ELC", "ELC"],
        process_type=["PRE", "PRE", "ELE", "ELE"],
        pypsa_carrier=["", "", "", ""],
    )

    agg = aggregate_flows(flows, level="sankey_overview", apply_netting=False)
    labels = set(agg["process_code"].astype(str)) | set(agg["commodity_code"].astype(str))
    assert not any("unknown" in x.lower() for x in labels)
    assert "Buildings" in labels or "Gas boiler" in labels
    assert "Electricity" in labels or "Electricity HV" in labels


def test_custom_level_has_no_unknown_on_energy_flows(mappings_dir: Path):
    """Bundled custom column is populated; aggregation never emits Unknown."""
    import pandas as pd
    from times_pypsa.aggregation import aggregate_flows, shared_aggregation_columns

    proc = pd.read_csv(mappings_dir / "mapping_processes.csv", nrows=0)
    com = pd.read_csv(mappings_dir / "mapping_commodities.csv", nrows=0)
    if "Technology (Process)" in proc.columns:
        proc = proc.rename(columns={"Technology (Process)": "Process"})
    shared = shared_aggregation_columns(
        pd.read_csv(mappings_dir / "mapping_processes.csv").rename(
            columns={"Technology (Process)": "Process"}
        ),
        pd.read_csv(mappings_dir / "mapping_commodities.csv"),
    )
    assert "custom" in shared

    flows = _toy_flows(
        proc_agg__custom=["", "Industry", "", "Industry"],
        com_agg__custom=["", "Electricity", "Electricity", ""],
        process=["Com Space Heat New Heat Pump", "Steel", "Gas boiler", "Cement"],
        commodity=["Heat", "Electricity HV", "Electricity HV", "Network gas"],
        sector=["COM", "IND", "RSD", "IND"],
        process_type=["PRE", "DMD", "PRE", "DMD"],
        process_agg=["", "Industry", "", "Industry"],
        pypsa_carrier=["Heat", "Electricity", "Electricity", "Natural Gas"],
    )
    agg = aggregate_flows(flows, level="custom", apply_netting=False)
    labels = set(agg["process_code"].astype(str)) | set(agg["commodity_code"].astype(str))
    assert not any("unknown" in x.lower() for x in labels)


def test_sankey_links_carry_process_commodity_kinds():
    from times_pypsa.aggregation import sankey_links_from_flows
    from times_pypsa.sankey_html import collect_typed_nodes, links_to_records, node_key

    flows = _toy_flows(
        proc_agg__custom=["Buildings"] * 4,
        com_agg__custom=["Electricity"] * 4,
    )
    # Use raw codes path via L2-like frame after aggregate
    agg = aggregate_flows(flows, level="custom", apply_netting=False)
    links = sankey_links_from_flows(agg)
    assert {"source_kind", "target_kind"}.issubset(links.columns)
    assert set(links["source_kind"]) | set(links["target_kind"]) == {"process", "commodity"}
    nodes = collect_typed_nodes(links)
    assert any(n["kind"] == "process" for n in nodes)
    assert any(n["kind"] == "commodity" for n in nodes)
    records = links_to_records(links)
    assert all("::" in r["source"] and "::" in r["target"] for r in records)
    assert node_key("process", "Buildings").startswith("process::")


def test_build_sankey_label_map_lists_member_codes():
    from times_pypsa.aggregation import build_sankey_label_map

    flows = _toy_flows(
        proc_agg__sankey_overview=["Buildings", "Buildings", "Power plants", "Power plants"],
        com_agg__sankey_overview=["Gas", "Gas", "Electricity", "Electricity"],
        process=["Boiler A", "Boiler A", "PV", "PV"],
        commodity=["Gas mix", "Gas mix", "HV elec", "HV elec"],
    )
    mp = build_sankey_label_map(flows, "sankey_overview")
    assert {"side", "sankey_label", "times_code", "times_description", "mapping_label", "value"} <= set(
        mp.columns
    )
    buildings = mp[(mp["side"] == "process") & (mp["sankey_label"] == "Buildings")]
    assert set(buildings["times_code"]) == {"P1"}
    assert buildings["times_description"].iloc[0] == "Boiler A"


def test_l0_alias_uses_sector_legacy_path():
    flows = _toy_flows(
        sector=["ELC", "ELC", "IND", "IND"],
        commodity_sector=["ELC", "ELC", "IND", "IND"],
        pypsa_carrier=["", "", "", ""],
    )

    agg = aggregate_flows(flows, level="L0", apply_netting=False)

    assert set(agg["process_code"]) == {"ELC", "IND"}
    assert set(agg["commodity_code"]) == {"ELC", "IND"}


def test_mapping_alias_falls_back_without_enriched_columns():
    flows = _toy_flows(
        process_agg=["Solar PV", "Solar PV", "Wind", "Wind"],
        pypsa_carrier=["electricity", "electricity", "electricity", "gas"],
    )

    agg = aggregate_flows(flows, level="mapping", apply_netting=False)

    assert set(agg["process_code"]) == {"Solar PV", "Wind"}
    assert set(agg["commodity_code"]) == {"electricity", "gas"}


def test_unknown_level_raises_with_available_levels():
    flows = _toy_flows(
        proc_agg__Sector=["ELC", "ELC", "IND", "IND"],
        com_agg__Sector=["ELC", "ELC", "IND", "IND"],
    )

    with pytest.raises(ValueError, match="sankey_overview") as exc_info:
        aggregate_flows(flows, level="sankey_overview", apply_netting=False)

    msg = str(exc_info.value)
    assert "Sector" in msg
    assert "proc_agg__sankey_overview" in msg or "com_agg__sankey_overview" in msg


def test_aggregation_reduces_unique_node_count():
    flows = _toy_flows(
        proc_agg__sankey_overview=["SameProc"] * 4,
        com_agg__sankey_overview=["SameCom"] * 4,
    )

    raw_nodes = flows["process_code"].nunique() + flows["commodity_code"].nunique()
    agg = aggregate_flows(flows, level="sankey_overview", apply_netting=False)
    agg_nodes = agg["process_code"].nunique() + agg["commodity_code"].nunique()

    assert raw_nodes == 4
    assert agg_nodes == 2
    assert agg_nodes < raw_nodes


def test_sankey_links_from_flows_after_column_aggregation():
    flows = _toy_flows(
        proc_agg__sankey_overview=["GroupA", "GroupA", "GroupB", "GroupB"],
        com_agg__sankey_overview=["CarrierX", "CarrierX", "CarrierY", "CarrierY"],
    )
    agg = aggregate_flows(flows, level="sankey_overview", apply_netting=False)
    links = sankey_links_from_flows(agg)

    assert not links.empty
    assert {"source", "target", "value"}.issubset(links.columns)

    fin = agg[agg["variable"].str.upper() == "VAR_FIN"].iloc[0]
    fout = agg[agg["variable"].str.upper() == "VAR_FOUT"].iloc[0]

    assert (
        (links["source"] == fin["commodity_code"])
        & (links["target"] == fin["process_code"])
    ).any()
    assert (
        (links["source"] == fout["process_code"])
        & (links["target"] == fout["commodity_code"])
    ).any()


def test_bundled_mapping_csvs_share_aggregation_columns(mappings_dir: Path):
    proc = pd.read_csv(mappings_dir / "mapping_processes.csv", nrows=0)
    com = pd.read_csv(mappings_dir / "mapping_commodities.csv", nrows=0)
    if "Technology (Process)" in proc.columns:
        proc = proc.rename(columns={"Technology (Process)": "Process"})

    shared = shared_aggregation_columns(proc, com)

    assert "Sector" in shared

    for col in ("Aggregation Level 1", "Aggregation Level 2"):
        if col in proc.columns and col in com.columns:
            assert col in shared

    if "sankey_overview" in proc.columns and "sankey_overview" in com.columns:
        assert "sankey_overview" in shared
        proc_full = pd.read_csv(mappings_dir / "mapping_processes.csv")
        com_full = pd.read_csv(mappings_dir / "mapping_commodities.csv")
        n_proc = proc_full["sankey_overview"].nunique(dropna=True)
        n_com = com_full["sankey_overview"].nunique(dropna=True)
        assert n_proc + n_com <= 20, (
            f"sankey_overview too fine: {n_proc} process + {n_com} commodity labels"
        )
    elif "sankey_overview" not in proc.columns:
        pytest.skip("sankey_overview not yet in bundled mapping CSVs")
