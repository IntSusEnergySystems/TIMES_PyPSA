"""Tests for collapsing balanced commodity hubs into process→process Sankey links."""

from __future__ import annotations

import logging

import pandas as pd
import pytest

from times_pypsa.aggregation import (
    CommodityImbalanceError,
    collapse_commodity_nodes,
    commodity_fin_fout_balance,
    imbalance_process_label,
)
from times_pypsa.sankey_html import collect_typed_nodes, links_to_records, node_kind_color


def _flows(
    rows: list[tuple[str, str, str, float, bool]] | None = None,
) -> pd.DataFrame:
    """rows: variable, process, commodity, value, exported."""
    if rows is None:
        rows = [
            ("VAR_FOUT", "Producer", "Elec", 10.0, False),
            ("VAR_FIN", "Consumer", "Elec", 10.0, True),
        ]
    return pd.DataFrame(
        [
            {
                "year": 2050,
                "region": "RW",
                "variable": var,
                "process_code": proc,
                "process": proc,
                "commodity_code": com,
                "commodity": com,
                "value": val,
                "exported": exp,
                "matched_categories": "cat" if exp else "",
            }
            for var, proc, com, val, exp in rows
        ]
    )


def test_balanced_commodity_becomes_process_to_process():
    links = collapse_commodity_nodes(_flows(), flow_threshold=0.0)
    assert not links.empty
    assert set(links["source_kind"]) == {"process"}
    assert set(links["target_kind"]) == {"process"}
    assert (links["source"] == "Producer").all()
    assert (links["target"] == "Consumer").all()
    assert abs(float(links["value"].sum()) - 10.0) < 1e-9
    assert (links["commodity"] == "Elec").all()
    # One exported endpoint is enough for a collapsed process→process flow
    assert set(links["export_status"]) == {"exported"}
    assert "VAR_FIn of Consumer" in str(links["export_detail"].iloc[0])


def test_collapse_export_or_rule_context_only():
    links = collapse_commodity_nodes(
        _flows(
            [
                ("VAR_FOUT", "Producer", "Elec", 10.0, False),
                ("VAR_FIN", "Consumer", "Elec", 10.0, False),
            ]
        )
    )
    assert set(links["export_status"]) == {"context"}


def test_collapse_export_or_rule_both_exported():
    links = collapse_commodity_nodes(
        _flows(
            [
                ("VAR_FOUT", "Producer", "Elec", 10.0, True),
                ("VAR_FIN", "Consumer", "Elec", 10.0, True),
            ]
        )
    )
    # Both endpoints exported → double-count (purple), not plain blue
    assert set(links["export_status"]) == {"double_count"}


def test_collapse_keeps_mixed_when_one_side_already_mixed():
    # Same producer commodity mixes exported + non-exported FOut rows
    links = collapse_commodity_nodes(
        _flows(
            [
                ("VAR_FOUT", "Producer", "Elec", 6.0, True),
                ("VAR_FOUT", "Producer", "Elec", 4.0, False),
                ("VAR_FIN", "Consumer", "Elec", 10.0, False),
            ]
        )
    )
    assert set(links["export_status"]) == {"mixed"}


def test_small_imbalance_warns_and_creates_artificial_node(caplog):
    flows = _flows(
        [
            ("VAR_FOUT", "Producer", "Gas", 100.0, False),
            ("VAR_FIN", "Consumer", "Gas", 95.0, False),
        ]
    )
    with caplog.at_level(logging.WARNING):
        links = collapse_commodity_nodes(flows, warn_rel=0.10)
    assert any("unbalanced" in r.message.lower() for r in caplog.records)
    assert any(r.levelno == logging.WARNING for r in caplog.records)
    kinds = set(links["source_kind"]) | set(links["target_kind"])
    assert "imbalance" in kinds
    assert "process" in kinds
    artificial = links[links["target_kind"] == "imbalance"]["target"].iloc[0]
    assert artificial.startswith("Unbalanced <10%:")
    assert "Gas" in artificial
    assert "FOut>FIn" in artificial
    assert (links[links["target_kind"] == "imbalance"]["imbalance_class"] == "unexplained").all()


def test_large_imbalance_errors_and_creates_artificial_node(caplog):
    flows = _flows(
        [
            ("VAR_FOUT", "Producer", "Oil", 100.0, False),
            ("VAR_FIN", "Consumer", "Oil", 20.0, False),
        ]
    )
    with caplog.at_level(logging.ERROR):
        links = collapse_commodity_nodes(flows, warn_rel=0.10)
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    artificial = links[links["target_kind"] == "imbalance"]["target"].iloc[0]
    assert artificial.startswith("Unbalanced ≥10%:")
    assert "Oil" in artificial
    # Matched portion still collapsed
    proc_links = links[
        (links["source_kind"] == "process") & (links["target_kind"] == "process")
    ]
    assert abs(float(proc_links["value"].sum()) - 20.0) < 1e-9
    resid = links[links["target_kind"] == "imbalance"]["value"].sum()
    assert abs(float(resid) - 80.0) < 1e-9


def test_raise_on_imbalance_optional():
    flows = _flows(
        [
            ("VAR_FOUT", "Producer", "Oil", 100.0, False),
            ("VAR_FIN", "Consumer", "Oil", 20.0, False),
        ]
    )
    with pytest.raises(CommodityImbalanceError):
        collapse_commodity_nodes(flows, raise_on_imbalance=True)


def test_final_demand_fout_only_is_expected_not_error(caplog):
    """FOut-only DEM commodity → magenta sink named after the demand process."""
    flows = _flows([("VAR_FOUT", "IntlAviation", "TAIF", 5.0, True)])
    with caplog.at_level(logging.INFO):
        links = collapse_commodity_nodes(flows)
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
    assert any("expected" in r.message.lower() for r in caplog.records)
    assert (links["target_kind"] == "imbalance").all()
    assert (links["source"] == "IntlAviation").all()
    assert (links["target"] == "IntlAviation").all()
    assert (links["imbalance_class"] == "final_demand").all()
    tip = str(links["imbalance_tooltip"].iloc[0])
    assert "Final energy demand" in tip
    assert "TAIF" in tip


def test_excluded_non_pj_consumer_is_expected_named_after_process(caplog):
    """Missing FIN explained by non-PJ building process → expected sink."""
    filtered = _flows(
        [
            ("VAR_FOUT", "Heater", "RH4F", 22.0, True),
            ("VAR_FIN", "OtherPJ", "RH4F", 2.0, False),
        ]
    )
    reference = _flows(
        [
            ("VAR_FOUT", "Heater", "RH4F", 22.0, True),
            ("VAR_FIN", "OtherPJ", "RH4F", 2.0, False),
            ("VAR_FIN", "Building4F", "RH4F", 20.0, False),
        ]
    )
    units = {"Heater": "PJ", "OtherPJ": "PJ", "Building4F": "MM2"}
    with caplog.at_level(logging.INFO):
        links = collapse_commodity_nodes(
            filtered,
            reference_flows=reference,
            process_activity_units=units,
            warn_rel=0.10,
        )
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
    resid = links[links["target_kind"] == "imbalance"]
    assert not resid.empty
    assert (resid["target"] == "Building4F").all()
    assert (resid["imbalance_class"] == "excluded_consumer").all()
    assert abs(float(resid["value"].sum()) - 20.0) < 1e-9
    tip = str(resid["imbalance_tooltip"].iloc[0])
    assert "MM2" in tip
    assert "Building4F" in tip
    # raise_on_imbalance must ignore expected sinks
    collapse_commodity_nodes(
        filtered,
        reference_flows=reference,
        process_activity_units=units,
        raise_on_imbalance=True,
    )


def test_unexplained_imbalance_still_errors_despite_reference(caplog):
    """Residual not matching non-PJ FIN mass stays an unexplained error."""
    filtered = _flows(
        [
            ("VAR_FOUT", "Producer", "Oil", 100.0, False),
            ("VAR_FIN", "Consumer", "Oil", 20.0, False),
        ]
    )
    # Reference has only a tiny non-PJ FIN — cannot explain the 80 PJ gap.
    reference = _flows(
        [
            ("VAR_FOUT", "Producer", "Oil", 100.0, False),
            ("VAR_FIN", "Consumer", "Oil", 20.0, False),
            ("VAR_FIN", "Tiny", "Oil", 1.0, False),
        ]
    )
    with caplog.at_level(logging.ERROR):
        links = collapse_commodity_nodes(
            filtered,
            reference_flows=reference,
            process_activity_units={"Producer": "PJ", "Consumer": "PJ", "Tiny": "MT"},
        )
    assert any(r.levelno == logging.ERROR for r in caplog.records)
    artificial = links[links["target_kind"] == "imbalance"]["target"].iloc[0]
    assert artificial.startswith("Unbalanced ≥10%:")


def test_neighbourhood_truncation_is_expected(caplog):
    """Missing opposite-side processes present only in reference → not an error."""
    # Neighbourhood keeps import + one consumer; industry consumer is outside.
    neighbourhood = _flows(
        [
            ("VAR_FOUT", "ImportGas", "Gas", 40.0, False),
            ("VAR_FIN", "FuelTech", "Gas", 10.0, True),
        ]
    )
    full = _flows(
        [
            ("VAR_FOUT", "ImportGas", "Gas", 40.0, False),
            ("VAR_FIN", "FuelTech", "Gas", 10.0, True),
            ("VAR_FIN", "Industry", "Gas", 30.0, False),
        ]
    )
    with caplog.at_level(logging.INFO):
        links = collapse_commodity_nodes(
            neighbourhood,
            reference_flows=full,
            process_activity_units={
                "ImportGas": "PJ",
                "FuelTech": "PJ",
                "Industry": "PJ",
            },
        )
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
    resid = links[links["target_kind"] == "imbalance"]
    assert not resid.empty
    assert (resid["imbalance_class"] == "neighbourhood_truncation").all()
    assert (resid["target"] == "Industry").all()
    assert abs(float(resid["value"].sum()) - 30.0) < 1e-9


def test_fout_only_in_view_but_consumers_in_reference_is_truncation(caplog):
    """FOut-only in a subset, with consumers in the full system → view truncation."""
    neighbourhood = _flows([("VAR_FOUT", "Heater", "Heat", 10.0, True)])
    full_system = _flows(
        [
            ("VAR_FOUT", "Heater", "Heat", 10.0, True),
            ("VAR_FIN", "Building", "Heat", 10.0, False),
        ]
    )
    with caplog.at_level(logging.INFO):
        links = collapse_commodity_nodes(
            neighbourhood,
            reference_flows=full_system,
            process_activity_units={"Heater": "PJ", "Building": "PJ"},
        )
    assert not any(r.levelno >= logging.WARNING for r in caplog.records)
    artificial = links[links["target_kind"] == "imbalance"]["target"].iloc[0]
    assert artificial == "Building"
    assert (links["imbalance_class"] == "neighbourhood_truncation").all()


def test_imbalance_nodes_use_magenta_family():
    color = node_kind_color("imbalance", "Unbalanced ≥10%: Oil (FOut>FIn, 80.0%)")
    # Magenta family: high R and B, lower G
    assert color.startswith("rgba(")
    nums = [int(x) for x in color[5:].split(")")[0].split(",")[:3]]
    r, g, b = nums
    assert r > g and b > g
    # Expected demand sinks keep the same hue family
    color2 = node_kind_color("imbalance", "IntlAviation")
    nums2 = [int(x) for x in color2[5:].split(")")[0].split(",")[:3]]
    assert nums2[0] > nums2[1] and nums2[2] > nums2[1]


def test_process_nodes_use_green_family():
    color = node_kind_color("process", "Fuel Tech - Diesel (TRA)")
    assert color.startswith("rgba(")
    nums = [int(x) for x in color[5:].split(")")[0].split(",")[:3]]
    r, g, b = nums
    # Green family: G dominant vs R/B (distinct from blue exported links)
    assert g > r and g > b


def test_collect_typed_nodes_includes_imbalance_tooltip():
    links = collapse_commodity_nodes(
        _flows([("VAR_FOUT", "DemandTech", "EndUse", 5.0, True)])
    )
    nodes = collect_typed_nodes(links)
    kinds = {n["kind"] for n in nodes}
    assert "imbalance" in kinds
    assert "process" in kinds
    assert "commodity" not in kinds
    imb = next(n for n in nodes if n["kind"] == "imbalance")
    assert imb["label"].startswith("U · ")
    assert imb["raw"] == "DemandTech"
    assert "Final energy demand" in imb["hover"]
    records = links_to_records(links)
    assert any("Final energy demand" in r["hover"] for r in records)


def test_balance_helper_and_label():
    bal = commodity_fin_fout_balance(
        _flows(
            [
                ("VAR_FOUT", "P", "X", 10.0, False),
                ("VAR_FIN", "C", "X", 10.0, False),
            ]
        )
    )
    assert bool(bal.iloc[0]["balanced"])
    label = imbalance_process_label("X", fout=12.0, fin=10.0, rel_err=0.166, warn_rel=0.1)
    assert label.startswith("Unbalanced ≥10%:")
    assert "FOut>FIn" in label
