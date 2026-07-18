"""Interactive QA HTML and multi-year Sankey tests."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from times_pypsa.qa import (
    generate_qa_report,
    prepare_export_sankey_links,
    prepare_system_sankey_links,
    tag_flows_with_rules,
)
from times_pypsa.sankey_html import CONTEXT_COLOR, EXPORTED_COLOR, MIXED_COLOR, links_to_records
from times_pypsa.pipeline import load_extraction_rules, load_metadata


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
    return year, tagged, {}, rules


def test_export_status_mixed_when_aggregated(tagged_2030):
    year, tagged, totals, rules = tagged_2030
    sample = tagged.head(200).copy()
    if len(sample) < 2:
        pytest.skip("Not enough tagged flows")
    sample.loc[sample.index[0], "exported"] = True
    sample.loc[sample.index[1], "exported"] = False
    sample.loc[sample.index[0], "process_agg"] = "Mixed test process"
    sample.loc[sample.index[1], "process_agg"] = "Mixed test process"
    sample.loc[sample.index[0], "pypsa_carrier"] = "Electricity"
    sample.loc[sample.index[1], "pypsa_carrier"] = "Electricity"
    links = prepare_system_sankey_links(sample, flow_threshold=0.0, apply_netting=False)
    mixed = links[links["export_status"] == "mixed"]
    assert not mixed.empty


def test_system_sankey_has_three_export_colours(tagged_2030):
    year, tagged, totals, rules = tagged_2030
    links = prepare_system_sankey_links(tagged, flow_threshold=1.0, apply_netting=True)
    statuses = set(links.get("export_status", pd.Series(dtype=str)).unique())
    assert statuses.issubset({"exported", "context", "mixed"})
    assert "context" in statuses or "exported" in statuses
    records = links_to_records(links)
    colours = {r["color"] for r in records}
    assert colours.issubset({EXPORTED_COLOR, CONTEXT_COLOR, MIXED_COLOR})


def test_system_sankey_wider_than_export_neighbourhood(tagged_2030):
    year, tagged, totals, rules = tagged_2030
    system = prepare_system_sankey_links(tagged, flow_threshold=0.0)
    export = prepare_export_sankey_links(tagged, flow_threshold=0.0)
    assert len(system) >= len(export)
    assert system["value"].sum() >= export["value"].sum()


def test_netting_reduces_or_equal_link_values(tagged_2030):
    year, tagged, totals, rules = tagged_2030
    gross = prepare_system_sankey_links(tagged, flow_threshold=0.0, apply_netting=False)
    netted = prepare_system_sankey_links(tagged, flow_threshold=0.0, apply_netting=True)
    assert netted["value"].sum() <= gross["value"].sum() + 1e-6


def test_generate_qa_report_all_years(tmp_path, vd_path, vdt_path, mappings_dir):
    out_dir = tmp_path / "qa_all"
    artifacts = generate_qa_report(
        vd_path,
        out_dir,
        year=None,
        vdt_file=vdt_path,
        mappings_dir=mappings_dir,
    )
    report = artifacts.get("report")
    assert report is not None
    assert report.name == "qa_report.html"
    html = report.read_text(encoding="utf-8")
    assert "Whole TIMES energy flows" in html
    assert "PyPSA export neighbourhood" in html
    assert "year-slider" in html
    assert "netting-toggle" in html
    assert "Mixed (exported + non-exported aggregated)" in html
    assert '"unit": "TWh"' in html
    assert any(p.name.startswith("qa_flows_") for p in out_dir.glob("qa_flows_*.csv"))


def test_generate_qa_report_pj_units(tmp_path, vd_path, vdt_path, mappings_dir, times_model):
    year = 2030 if 2030 in times_model.years else times_model.years[0]
    out_dir = tmp_path / "qa_pj"
    generate_qa_report(
        vd_path,
        out_dir,
        year=year,
        vdt_file=vdt_path,
        mappings_dir=mappings_dir,
        units="pj",
    )
    html = (out_dir / "qa_report.html").read_text(encoding="utf-8")
    assert '"unit": "PJ"' in html
    flows = pd.read_csv(out_dir / f"qa_flows_{year}.csv")
    assert "PJ" in flows.columns
    assert "TWh" not in flows.columns


def test_generate_qa_report_single_year(tmp_path, vd_path, vdt_path, mappings_dir, times_model):
    year = 2030 if 2030 in times_model.years else times_model.years[0]
    out_dir = tmp_path / "qa_one"
    artifacts = generate_qa_report(
        vd_path,
        out_dir,
        year=year,
        vdt_file=vdt_path,
        mappings_dir=mappings_dir,
    )
    assert (out_dir / "qa_report.html").exists()
    assert (out_dir / f"qa_flows_{year}.csv").exists()
    flows = pd.read_csv(out_dir / f"qa_flows_{year}.csv")
    assert "TWh" in flows.columns
