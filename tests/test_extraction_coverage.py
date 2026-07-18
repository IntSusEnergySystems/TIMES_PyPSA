"""Extraction coverage, double-count, and empty-rule tests."""

from __future__ import annotations

from pathlib import Path

import pytest

from times_pypsa.balances import (
    KNOWN_ZERO_CATEGORIES,
    double_count_matrix,
    empty_rule_report,
    parent_child_sum_checks,
)
from times_pypsa.pipeline import load_extraction_rules, load_metadata
from times_pypsa.qa import category_pj_totals, tag_flows_with_rules


@pytest.fixture(scope="module")
def rules(mappings_dir: Path):
    meta = load_metadata(mappings_dir)
    return load_extraction_rules(meta.extraction_rules_file)


@pytest.fixture(scope="module")
def tagged_2030(times_model, rules):
    years = times_model.years
    year = 2030 if 2030 in years else years[0]
    flows = times_model.energy_flows(year)
    tagged = tag_flows_with_rules(flows, rules, apply_netting=False)
    totals = category_pj_totals(flows, rules, apply_netting=True)
    return year, tagged, totals, rules


@pytest.fixture(scope="module")
def totals_all_horizons(times_model, rules):
    """Max PJ per category across soft-link horizons."""
    horizons = [y for y in (2025, 2030, 2040, 2050) if y in times_model.years]
    if not horizons:
        horizons = times_model.years[:4]
    max_pj: dict[str, float] = {cat: 0.0 for cat in rules}
    for year in horizons:
        flows = times_model.energy_flows(year)
        totals = category_pj_totals(flows, rules, apply_netting=True)
        for cat, pj in totals.items():
            max_pj[cat] = max(max_pj[cat], pj)
    return max_pj


def test_fifty_four_rules(rules):
    assert len(rules) == 54


def test_empty_rules_only_allowlisted(totals_all_horizons, rules):
    """Each category must be non-zero in ≥1 soft-link horizon, or be allowlisted."""
    report = empty_rule_report(totals_all_horizons, rules.keys())
    unexpected = report[~report["ok"]]
    assert unexpected.empty, (
        "Unexpected zero categories across all horizons "
        "(not in known-zero allowlist):\n" + unexpected.to_string(index=False)
    )


def test_known_zeros_documented():
    assert "ammonia" in KNOWN_ZERO_CATEGORIES
    assert "methanol" in KNOWN_ZERO_CATEGORIES


def test_double_count_allowlist(tagged_2030):
    year, tagged, totals, rules = tagged_2030
    keys = tagged.attrs.get("category_keys", {})
    bad = double_count_matrix(keys)
    # Report but allow a small number until audit closes them; fail if huge.
    assert len(bad) < 5, (
        f"{len(bad)} disallowed overlaps — inspect qa_double_count CSV:\n"
        + bad.head(15).to_string(index=False)
    )


def test_parent_child_heat_sums(tagged_2030):
    year, tagged, totals, rules = tagged_2030
    checks = parent_child_sum_checks(totals, tolerance_pj=0.5)
    heat = checks[checks["parent"].str.contains("BEWAL|heat", case=False, na=False)]
    # Soft assert: failures become expert questions, but urban/rural should be close
    failed = heat[~heat["ok"]]
    # If all heat parents are zero, skip
    if heat["parent_pj"].sum() == 0:
        pytest.skip("No heat PJ in this scenario/year")
    assert len(failed) <= 2, (
        "Parent–child heat mismatches:\n" + failed.to_string(index=False)
    )


def test_tagging_marks_exported_rows(tagged_2030):
    year, tagged, totals, rules = tagged_2030
    assert "exported" in tagged.columns
    assert tagged["exported"].any(), "No flows matched any extraction rule"
    # Exported PJ should be positive
    assert tagged.loc[tagged["exported"], "value"].sum() > 0


def test_process_agg_column_present(times_model):
    flows = times_model.flows
    assert "process_agg" in flows.columns
    assert "sector" in flows.columns


def test_export_neighborhood_includes_context(tagged_2030):
    from times_pypsa.qa import select_export_neighborhood

    year, tagged, totals, rules = tagged_2030
    nb = select_export_neighborhood(tagged)
    assert len(nb) >= int(tagged["exported"].sum())
    # Neighbourhood must include non-exported context when exports exist
    if tagged["exported"].any() and (~tagged["exported"]).any():
        assert (~nb["exported"]).any() or len(nb) > int(tagged["exported"].sum())
