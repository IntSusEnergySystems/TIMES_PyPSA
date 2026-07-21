"""Interactive QA HTML and multi-year Sankey tests."""

from __future__ import annotations

import json
import re
from pathlib import Path

import pandas as pd
import pytest

from times_pypsa.qa import (
    generate_qa_report,
    prepare_export_sankey_links,
    prepare_system_sankey_links,
    tag_flows_with_rules,
)
from times_pypsa.sankey_html import (
    CONTEXT_COLOR,
    DOUBLE_COUNT_COLOR,
    EXPORTED_COLOR,
    MIXED_COLOR,
    build_sankey_dataset,
    collect_typed_nodes,
    ensure_sankey_node_coverage,
    links_to_records,
    validate_sankey_chart,
)
from times_pypsa.pipeline import load_extraction_rules, load_metadata


def _charts_from_qa_html(html: str) -> list[dict]:
    match = re.search(
        r"const TIMES_PYSA_SANKEYS = (\[.*?\]);\s*\nfunction timesPypsaSankeyPlot",
        html,
        re.S,
    )
    assert match is not None, "embedded Sankey chart payload missing"
    return json.loads(match.group(1))


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
    # Force both rows into the same aggregated nodes (column-based level)
    for col in (
        "process_agg",
        "agg_level_2",
        "proc_agg__Aggregation Level 2",
        "proc_agg__custom",
    ):
        if col in sample.columns:
            sample.loc[sample.index[0], col] = "Mixed test process"
            sample.loc[sample.index[1], col] = "Mixed test process"
    for col in ("pypsa_carrier", "com_agg__Aggregation Level 2", "com_agg__custom"):
        if col in sample.columns:
            sample.loc[sample.index[0], col] = "Electricity"
            sample.loc[sample.index[1], col] = "Electricity"
    links = prepare_system_sankey_links(
        sample, flow_threshold=0.0, apply_netting=False, collapse_commodities=False
    )
    mixed = links[links["export_status"] == "mixed"]
    assert not mixed.empty


def test_collapsed_system_sankey_is_mostly_process_nodes(tagged_2030):
    year, tagged, totals, rules = tagged_2030
    links = prepare_system_sankey_links(
        tagged, flow_threshold=1.0, apply_netting=True, collapse_commodities=True
    )
    kinds = set(links["source_kind"]) | set(links["target_kind"])
    assert "process" in kinds
    assert "commodity" not in kinds
    # Imbalance residuals may appear as artificial process nodes
    assert kinds <= {"process", "imbalance"}
    nodes = collect_typed_nodes(links)
    assert any(n["kind"] == "process" for n in nodes)
    year, tagged, totals, rules = tagged_2030
    links = prepare_system_sankey_links(tagged, flow_threshold=1.0, apply_netting=True)
    statuses = set(links.get("export_status", pd.Series(dtype=str)).unique())
    assert statuses.issubset({"exported", "context", "mixed", "double_count"})
    assert "context" in statuses or "exported" in statuses or "double_count" in statuses
    records = links_to_records(links)
    colours = {r["color"] for r in records}
    assert colours.issubset(
        {EXPORTED_COLOR, CONTEXT_COLOR, MIXED_COLOR, DOUBLE_COUNT_COLOR}
    )


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


def test_sankey_chart_payload_covers_gross_endpoints(tagged_2030):
    year, tagged, totals, rules = tagged_2030
    netted_links = prepare_system_sankey_links(
        tagged, flow_threshold=0.0, apply_netting=True
    )
    gross_links = prepare_system_sankey_links(
        tagged, flow_threshold=0.0, apply_netting=False
    )
    chart = build_sankey_dataset(
        chart_id="system",
        title="test",
        years=[year],
        netted_by_year={year: links_to_records(netted_links)},
        gross_by_year={year: links_to_records(gross_links)},
        nodes_by_year={
            year: collect_typed_nodes(
                pd.concat([netted_links, gross_links], ignore_index=True)
            )
        },
    )
    assert validate_sankey_chart(chart) == []
    assert len(chart["gross"][str(year)]) >= len(chart["netted"][str(year)])


def test_ensure_sankey_node_coverage_adds_missing_keys():
    nodes = [{"key": "process::A", "label": "A", "kind": "process", "raw": "A", "color": "", "hover": "A"}]
    links = [{"source": "process::A", "target": "process::B", "value": 1.0, "color": "", "hover": ""}]
    covered = ensure_sankey_node_coverage(nodes, links)
    keys = {n["key"] for n in covered}
    assert keys == {"process::A", "process::B"}


def test_generate_qa_report_all_years(
    tmp_path, qa_vd_path, qa_vdt_path, mappings_dir, qa_model
):
    out_dir = tmp_path / "qa_all"
    artifacts = generate_qa_report(
        qa_vd_path,
        out_dir,
        year=None,
        vdt_file=qa_vdt_path,
        mappings_dir=mappings_dir,
        model=qa_model,
    )
    report = artifacts.get("report")
    assert report is not None
    assert report.name == "qa_report.html"
    html = report.read_text(encoding="utf-8")
    assert "Whole TIMES energy flows" in html
    assert "PyPSA export neighbourhood" in html
    assert "year-slider" in html
    assert "netting-toggle" in html
    assert "Plotly.purge" in html
    assert "Plotly.newPlot" in html
    charts = _charts_from_qa_html(html)
    issues = [issue for chart in charts for issue in validate_sankey_chart(chart)]
    assert issues == []
    assert "Mixed (same endpoint mixes exported + non-exported)" in html
    assert "Double-count (FOut+FIn both exported)" in html
    assert '"unit": "TWh"' in html
    assert any(p.name.startswith("qa_flows_") for p in out_dir.glob("qa_flows_*.csv"))


def test_generate_qa_report_pj_units(
    tmp_path, qa_vd_path, qa_vdt_path, mappings_dir, qa_model
):
    year = 2030 if 2030 in qa_model.years else qa_model.years[0]
    out_dir = tmp_path / "qa_pj"
    generate_qa_report(
        qa_vd_path,
        out_dir,
        year=year,
        vdt_file=qa_vdt_path,
        mappings_dir=mappings_dir,
        units="pj",
        model=qa_model,
    )
    html = (out_dir / "qa_report.html").read_text(encoding="utf-8")
    assert '"unit": "PJ"' in html
    flows = pd.read_csv(out_dir / f"qa_flows_{year}.csv")
    assert "PJ" in flows.columns
    assert "TWh" not in flows.columns


def test_generate_qa_report_single_year(
    tmp_path, qa_vd_path, qa_vdt_path, mappings_dir, qa_model
):
    year = 2030 if 2030 in qa_model.years else qa_model.years[0]
    out_dir = tmp_path / "qa_one"
    artifacts = generate_qa_report(
        qa_vd_path,
        out_dir,
        year=year,
        vdt_file=qa_vdt_path,
        mappings_dir=mappings_dir,
        model=qa_model,
    )
    assert (out_dir / "qa_report.html").exists()
    assert (out_dir / f"qa_flows_{year}.csv").exists()
    flows = pd.read_csv(out_dir / f"qa_flows_{year}.csv")
    assert "TWh" in flows.columns
