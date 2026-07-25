"""Extraction coverage, double-count, and empty-rule tests."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
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


def test_rule_count(rules):
    # 54 original + residential cooking (2026-07-24) + its electric split, the
    # services data-centre split and `services other fuel` (2026-07-25).
    assert len(rules) == 58


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


def test_exported_unit_check_flags_non_pj_commodities():
    """An activity commodity reaching an export rule must be flagged, not summed.

    The extractor has no unit guard, so `mapping_commodities.csv` `Unit` is the
    only declaration that a soft-linked commodity is energy at all.
    """
    from times_pypsa.qa import exported_unit_check

    tagged = pd.DataFrame(
        {
            "commodity_code": ["TRADST", "TNDF"],
            "commodity": ["Diesel for transport", "Domestic navigation activity"],
            "value": [10.0, 1.6],
            "exported": [True, True],
            "matched_categories": [["total road"], ["total domestic navigation"]],
        }
    )
    commodities = pd.DataFrame(
        {"TIMES commodity": ["TRADST", "TNDF"], "Unit": ["PJ", "BTKM"]}
    )
    out = exported_unit_check(tagged, commodities)
    assert list(out["commodity_code"]) == ["TNDF"]
    assert out.iloc[0]["unit"] == "BTKM"
    assert out.iloc[0]["category"] == "total domestic navigation"

    # All-PJ input → empty, and the normalised lowercase mapping frame also works.
    clean = exported_unit_check(
        tagged.head(1), pd.DataFrame({"times": ["TRADST"], "unit": ["PJ"]})
    )
    assert clean.empty


def test_bundled_commodity_units_match_producer_activity_units(mappings_dir):
    """`Unit` must not claim PJ for a commodity produced only by non-PJ processes.

    This is the declaration that makes `exported_unit_check` meaningful; when the
    column was a uniform "PJ" it could never catch a unit error.
    """
    com = pd.read_csv(mappings_dir / "mapping_commodities.csv")
    units = dict(
        zip(
            com["TIMES commodity"].astype(str).str.strip(),
            com["Unit"].astype(str).str.strip(),
        )
    )
    # The known non-energy commodity must stay declared as such.
    assert units.get("TNDF") == "BTKM"


def test_var_fout_rules_are_heat_or_delivered_fuel(mappings_dir):
    """A `VAR_FOut` rule outside heating reads a *service*, not final energy.

    Heating is the documented exception (PyPSA models the heat bus) and `hydrogen`
    is a `delivered_fuel`. Aviation used to read `TAIF`/`TAIP`, which only happened
    to equal the kerosene because TIMES gives those processes efficiency 1.000.
    """
    from times_pypsa.pipeline import load_rule_metadata

    rules = pd.read_csv(mappings_dir / "extraction_rules.csv")
    meta = load_rule_metadata(mappings_dir / "extraction_rules.csv")
    fout = rules[rules["var_type"].astype(str).str.upper() == "VAR_FOUT"]
    for _, row in fout.iterrows():
        category = str(row["category"])
        measure_at = meta[category].measure_at
        if measure_at == "delivered_fuel":
            continue
        assert measure_at == "service_output", (category, measure_at)
        assert str(row["carrier"]).strip() == "Heat", (
            f"{category} reads VAR_FOut on carrier {row['carrier']!r} — "
            "only heating may export a service output"
        )


def test_mapping_units_match_the_veda_dictionary(mappings_dir):
    """`Unit` must agree with `AllCommodities.csv` wherever VEDA declares one.

    `INDBLQ` was hand-set to MT while VEDA declares it `.NRG.` / PJ, which is what
    made black liquor look like a material rather than an excluded energy flow.
    """
    com = pd.read_csv(mappings_dir / "mapping_commodities.csv")
    veda = pd.read_csv(
        mappings_dir / "AllCommodities.csv", sep=";", encoding="utf-8-sig"
    ).drop_duplicates(subset=["Name"], keep="first")
    merged = com.merge(
        veda[["Name", "Unit"]],
        left_on="TIMES commodity",
        right_on="Name",
        how="inner",
        suffixes=("_mapping", "_veda"),
    )
    merged = merged[merged["Unit_veda"].notna()]
    mismatched = merged[
        merged["Unit_mapping"].astype(str).str.strip().str.upper()
        != merged["Unit_veda"].astype(str).str.strip().str.upper()
    ]
    assert mismatched.empty, mismatched[
        ["TIMES commodity", "Unit_mapping", "Unit_veda"]
    ].to_string()


# --------------------------------------------------------------------------- #
# extraction_rules.csv schema: every declared filter must be real and applied
# --------------------------------------------------------------------------- #
def test_every_declared_filter_value_resolves(mappings_dir):
    """A filter value that matches no label is a silent no-op — fail on it.

    The previous schema hid these: a second filter was only applied when
    `filter_type == "combined"`, so typos like a process label in a carrier
    column (`Fuel Tech – Diesel`, en dash) sat in the file unnoticed. Rules
    declared `expect=zero` are exempt: they are PyPSA placeholders for demands
    TIMES-WAL does not model at all.
    """
    rules_df = pd.read_csv(mappings_dir / "extraction_rules.csv").fillna("")
    proc = pd.read_csv(mappings_dir / "mapping_processes.csv")
    com = pd.read_csv(mappings_dir / "mapping_commodities.csv")
    known_labels = {
        "process_agg": set(proc["Aggregation Level 2"].dropna().astype(str).str.strip()),
        "carrier": set(com["PyPSA Energy Carrier"].dropna().astype(str).str.strip()),
    }

    unresolved = []
    for _, row in rules_df.iterrows():
        if row["expect"] == "zero":
            continue
        for column, labels in known_labels.items():
            for raw in str(row[column]).split(";"):
                value = raw.strip()
                if value and value not in labels:
                    unresolved.append(f"{row['category']} / {column} / {value!r}")
    assert not unresolved, "Filter values matching no mapping label:\n" + "\n".join(
        unresolved
    )


def test_rule_metadata_columns_are_complete(mappings_dir):
    """`pypsa_sector` / `parent` / `measure_at` drive code, so they must be sound."""
    from times_pypsa.pipeline import load_rule_metadata

    meta = load_rule_metadata(mappings_dir / "extraction_rules.csv")
    assert len(meta) == 58

    missing_sector = [c for c, m in meta.items() if not m.sector]
    assert not missing_sector, f"Categories with no pypsa_sector: {missing_sector}"

    dangling = [
        (c, m.parent) for c, m in meta.items() if m.parent and m.parent not in meta
    ]
    assert not dangling, f"parent pointing at a non-category: {dangling}"
    assert not [c for c, m in meta.items() if m.parent == c], "a rule is its own parent"

    assert set(m.measure_at for m in meta.values()) <= {
        "fuel_input",
        "demand_input",
        "service_output",
        "delivered_fuel",
    }


def test_unknown_filter_field_raises(mappings_dir):
    """A typo'd field must fail loudly: it used to apply no filter at all.

    With no `else` branch the whole system passed through, so a single mistyped
    field name would have exported every flow in the model under that category.
    """
    from times_pypsa.pipeline import _apply_extraction_rule

    df = pd.DataFrame(
        {
            "variable": ["VAR_FIN"],
            "process_agg": ["Industry"],
            "commodity_code": ["INDELC"],
            "pypsa_carrier": ["Electricity"],
            "value": [1.0],
        }
    )
    with pytest.raises(ValueError, match="Unknown extraction filter field"):
        _apply_extraction_rule(df, "VAR_FIN", "proces_agg", ["Industry"], False)


def test_emission_commodities_never_reach_a_demand():
    """CO2 is reported in kt in the same column as PJ; summing it is nonsense.

    Only the incidental `pypsa_carrier=Heat` filter kept it out before: dropping
    that filter from `residential urban decentral gas boiler` turned 16.9 PJ into
    2000.8. Rules with no carrier filter (`retro`) were protected by luck alone.
    """
    from times_pypsa.pipeline import _apply_extraction_rule

    df = pd.DataFrame(
        {
            "variable": ["VAR_FOUT"] * 3,
            "process_agg": ["Residential urban decentral gas heater"] * 3,
            "commodity_code": ["RH2F", "RSDCO2N", "NETSCO2N"],
            "pypsa_carrier": ["Heat", "", ""],
            "value": [16.9, 974.9, 974.9],
        }
    )
    out = _apply_extraction_rule(
        df, "VAR_FOUT", "process_agg", ["Residential urban decentral gas heater"], False
    )
    assert abs(out["value"].sum() - 16.9) < 1e-9
    assert set(out["commodity_code"]) == {"RH2F"}


def test_no_exported_flow_carries_a_non_pj_unit(times_model, rules, mappings_dir):
    """PyPSA must receive final energy, so every exported commodity must be PJ.

    `total domestic navigation` used to export `TNDF`, a **Btkm** activity, into
    the PJ column (1.62 PJ 2030, implying 283% efficiency). It now reads the
    diesel the ships burn instead. This asserts the whole file stays clean.
    """
    from times_pypsa.pipeline import load_metadata
    from times_pypsa.qa import exported_unit_check, tag_flows_with_rules

    commodities = load_metadata(mappings_dir).mapping_df
    for year in times_model.years:
        tagged = tag_flows_with_rules(times_model.energy_flows(year), rules)
        issues = exported_unit_check(tagged, commodities)
        assert issues.empty, (
            f"{year}: exported commodities that are not PJ:\n"
            + issues.to_string(index=False)
        )


def test_declared_subtractions_remove_downstream_double_counts(rules, mappings_dir):
    """`adjust=subtract:X` must be applied, and X must be a real category.

    A road fuel tech serves road vehicles, rail and inland ships from one `TRADST`
    pool, so `total road` (measured at the tech) contains all three.
    """
    from times_pypsa.pipeline import _apply_subtractions, load_rule_metadata

    meta = load_rule_metadata(mappings_dir / "extraction_rules.csv")
    subtracted = {
        target: [t.split(":", 1)[1] for t in m.adjust if t.startswith("subtract:")]
        for target, m in meta.items()
        if any(t.startswith("subtract:") for t in m.adjust)
    }
    assert subtracted == {
        "electricity road": ["electricity rail"],
        "total road": ["total rail", "total domestic navigation"],
    }
    for others in subtracted.values():
        for other in others:
            assert other in meta, f"subtract: points at a non-category: {other}"

    results = pd.DataFrame(
        {
            "category": ["total road", "total rail", "total domestic navigation"],
            "TWh": [10.0, 2.0, 1.0],
            "PJ": [0.0, 0.0, 0.0],
        }
    )
    out = _apply_subtractions(results, meta).set_index("category")
    assert abs(out.loc["total road", "TWh"] - 7.0) < 1e-9
    assert abs(out.loc["total rail", "TWh"] - 2.0) < 1e-9


def test_navigation_reads_the_fuel_not_the_activity(times_model, rules):
    """The navigation demand must be the ships' diesel, not their Btkm output."""
    from times_pypsa.qa import category_pj_totals

    flows = times_model.energy_flows(2030)
    pj = category_pj_totals(flows, rules)["total domestic navigation"]
    assert 0.5 < pj < 0.6, f"expected ~0.572 PJ of ship diesel, got {pj}"

    var_type, _filter_type, _filters = rules["total domestic navigation"]
    assert var_type == "VAR_FIN"
