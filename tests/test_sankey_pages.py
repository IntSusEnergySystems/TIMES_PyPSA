"""Per-horizon Sankey pages written into a report folder."""

from __future__ import annotations

import json
import re

import pytest

from times_pypsa.sankey_pages import (
    DEFAULT_INDEX_NAME,
    DEFAULT_PAGE_LEVELS,
    export_sankey_pages,
    level_slug,
    sankey_page_name,
    sankey_page_names,
)
from times_pypsa.sankey_html import validate_sankey_chart


def _charts(html: str) -> list[dict]:
    match = re.search(
        r"const TIMES_PYSA_SANKEYS = (\[.*?\]);\s*\nfunction timesPypsaSankeyPlot",
        html,
        re.S,
    )
    assert match is not None, "embedded Sankey chart payload missing"
    return json.loads(match.group(1))


def test_level_slug_uses_documented_alias():
    assert level_slug("Aggregation Level 2") == "mapping"
    assert level_slug("custom") == "custom"
    assert level_slug("sankey_overview") == "sankey-overview"


def test_page_names_match_what_is_written(tmp_path, qa_model, mappings_dir):
    """The declared file list is the written file list.

    A Snakemake rule declares its outputs from `sankey_page_names` before the
    data exists; if the two ever disagree the rule fails on a missing output.
    """
    years = qa_model.years[:1]
    artifacts = export_sankey_pages(
        tmp_path,
        mappings_dir=mappings_dir,
        model=qa_model,
        years=years,
    )
    declared = set(sankey_page_names(years, DEFAULT_PAGE_LEVELS))
    written = {p.name for p in tmp_path.glob("*.html")}
    assert declared == written
    assert {p.name for p in artifacts.values()} == written


def test_pages_render_one_year_without_a_dead_slider(tmp_path, qa_model, mappings_dir):
    year = qa_model.years[0]
    export_sankey_pages(
        tmp_path,
        mappings_dir=mappings_dir,
        model=qa_model,
        years=[year],
        agg_levels=["custom"],
    )
    page = tmp_path / sankey_page_name("custom", year)
    html = page.read_text()

    charts = _charts(html)
    assert len(charts) == 1
    assert charts[0]["years"] == [year]
    assert validate_sankey_chart(charts[0]) == []
    assert charts[0]["netted"][str(year)], "no netted links on the page"

    # Single-year chart: the year is a caption, not a one-stop range input.
    assert 'type="range"' not in html
    assert "netting-toggle" in html


def test_multi_year_report_keeps_its_slider(tmp_path, qa_model, mappings_dir):
    """The QA report path is unchanged: >1 year still gets the range input."""
    from times_pypsa.sankey_html import (
        build_sankey_dataset,
        render_interactive_sankey_section,
    )

    chart = build_sankey_dataset(
        chart_id="t",
        title="t",
        years=[2030, 2040],
        netted_by_year={},
        gross_by_year={},
    )
    assert 'type="range"' in render_interactive_sankey_section(chart)


def test_index_links_every_page(tmp_path, qa_model, mappings_dir):
    years = qa_model.years[:2] or qa_model.years[:1]
    export_sankey_pages(
        tmp_path,
        mappings_dir=mappings_dir,
        model=qa_model,
        years=years,
    )
    index = (tmp_path / DEFAULT_INDEX_NAME).read_text()
    for level in DEFAULT_PAGE_LEVELS:
        for year in years:
            assert sankey_page_name(level, year) in index


def test_year_absent_from_the_vd_still_gets_a_page(tmp_path, qa_model, mappings_dir):
    """A declared output must exist even when TIMES has nothing for that year.

    Snakemake fails the rule on a missing output, so a horizon the .vd does not
    cover has to produce a page that says so rather than no page at all.
    """
    ghost = max(qa_model.years) + 5
    export_sankey_pages(
        tmp_path,
        mappings_dir=mappings_dir,
        model=qa_model,
        years=[ghost],
        agg_levels=["custom"],
    )
    page = tmp_path / sankey_page_name("custom", ghost)
    assert page.exists()
    page_html = page.read_text()
    charts = _charts(page_html)
    assert charts[0]["netted"][str(ghost)] == []
    # Visible on the page, not just in the log.
    assert f"No energy flows for {ghost}" in page_html
    assert "no flows" in (tmp_path / DEFAULT_INDEX_NAME).read_text()


def test_unknown_level_fails_fast(tmp_path, qa_model, mappings_dir):
    with pytest.raises(ValueError, match="Unknown Sankey aggregation level"):
        export_sankey_pages(
            tmp_path,
            mappings_dir=mappings_dir,
            model=qa_model,
            years=qa_model.years[:1],
            agg_levels=["Aggregation Level 7"],
        )
    assert not list(tmp_path.glob("*.html"))


def test_requires_a_source(tmp_path):
    with pytest.raises(ValueError, match="vd_file"):
        export_sankey_pages(tmp_path)
