"""HTML indicator pages written into a report folder."""

from __future__ import annotations

import json
import re

import pytest

from times_pypsa.indicator_pages import (
    DEFAULT_INDEX_NAME,
    GROUPS,
    _assign_colors,
    export_indicator_pages,
    indicator_frames,
    indicator_page_name,
    indicator_page_names,
)


def _catalogue(html: str) -> dict:
    match = re.search(
        r"const TIMES_PYPSA_CATALOGUE = (\{.*?\});\s*\nfunction timesPypsaSparkline",
        html,
        re.S,
    )
    assert match is not None, "embedded catalogue payload missing"
    return json.loads(match.group(1))


def _charts(html: str) -> list[dict]:
    match = re.search(
        r"const TIMES_PYPSA_INDICATORS = (\[.*?\]);\s*\nfunction timesPypsaIndicatorPlot",
        html,
        re.S,
    )
    assert match is not None, "embedded chart payload missing"
    return json.loads(match.group(1))


def test_declared_page_names_are_the_written_page_names(tmp_path, times_model, mappings_dir):
    """A Snakemake rule fixes its outputs before the data exists.

    If the helper and the writer ever disagree the run fails on a missing output
    file with no explanation, so they are checked against each other here.
    """
    export_indicator_pages(tmp_path, mappings_dir=mappings_dir, model=times_model)
    declared = set(indicator_page_names())
    written = {p.name for p in tmp_path.glob("*.html")}
    assert declared == written


def test_every_group_gets_a_page_even_when_empty(tmp_path, times_model, mappings_dir):
    """The caller declared the file; a silent gap would fail the run instead."""
    artifacts = export_indicator_pages(
        tmp_path, mappings_dir=mappings_dir, model=times_model
    )
    for key, _, _ in GROUPS:
        assert key in artifacts
        assert artifacts[key].exists()
    assert artifacts["index"].name == DEFAULT_INDEX_NAME


def test_pages_embed_a_chart_per_table_with_matching_years(
    tmp_path, times_model, mappings_dir
):
    years = times_model.years[:2]
    artifacts = export_indicator_pages(
        tmp_path, mappings_dir=mappings_dir, model=times_model, years=years
    )
    seen = 0
    for key, _, _ in GROUPS:
        if key == "catalogue":
            continue  # a table, not charts
        charts = _charts(artifacts[key].read_text(encoding="utf-8"))
        for chart in charts:
            assert chart["years"] == years
            assert chart["traces"], f"{chart['id']} has no series"
            for trace in chart["traces"]:
                assert len(trace["values"]) == len(years)
                assert trace["color"].startswith("#")
            if chart["total"] is not None:
                assert len(chart["total"]) == len(years)
            seen += 1
    assert seen > 0


def test_colors_are_unique_within_one_chart():
    """`Electricity` is a carrier on one page and a sector on another; without
    the collision fix it shares Industry's blue and the bands merge."""
    labels = ["Agriculture", "Electricity", "Industry", "Residential", "Tertiary"]
    colors = _assign_colors(labels)
    assert len(set(colors.values())) == len(labels)


def test_csv_tables_are_written_beside_the_pages(tmp_path, times_model, mappings_dir):
    artifacts = export_indicator_pages(
        tmp_path, mappings_dir=mappings_dir, model=times_model
    )
    csvs = {k: v for k, v in artifacts.items() if k.startswith("csv_")}
    assert csvs
    for key, path in csvs.items():
        assert path.suffix == ".csv"
        # ASCII-only names: these land in an rsync'd web folder.
        assert path.name.isascii(), path.name
        head = path.read_text(encoding="utf-8").splitlines()
        if key == "csv_catalogue":
            continue  # tidy, not wide; checked in test_catalogue_csv_is_tidy
        # Chart tables lead with the Total row, as the report tables do.
        assert head[1].startswith("Total,")


def test_no_csv_flag_writes_only_html(tmp_path, times_model, mappings_dir):
    export_indicator_pages(
        tmp_path, mappings_dir=mappings_dir, model=times_model, write_csv=False
    )
    assert not list(tmp_path.glob("*.csv"))
    assert list(tmp_path.glob("*.html"))


def test_pages_cross_link_and_name_the_scenario(tmp_path, times_model, mappings_dir):
    artifacts = export_indicator_pages(
        tmp_path,
        mappings_dir=mappings_dir,
        model=times_model,
        scenario_label="toy scenario",
    )
    html = artifacts["demand"].read_text(encoding="utf-8")
    assert "toy scenario" in html
    assert DEFAULT_INDEX_NAME in html
    for key, _, _ in GROUPS:
        if key != "demand":
            assert indicator_page_name(key) in html
    index = artifacts["index"].read_text(encoding="utf-8")
    for key, _, _ in GROUPS:
        assert indicator_page_name(key) in index


def test_indicator_frames_matches_the_written_csvs(tmp_path, times_model, mappings_dir):
    frames = indicator_frames(times_model)
    artifacts = export_indicator_pages(
        tmp_path, mappings_dir=mappings_dir, model=times_model
    )
    for key, frame in frames.items():
        path = artifacts.get(f"csv_{key}")
        assert path is not None, f"no CSV written for {key}"
        assert frame.index[0] == "Total"
        assert path.read_text(encoding="utf-8").count("\n") == len(frame) + 1


def test_requires_a_model_or_a_vd(tmp_path, mappings_dir):
    with pytest.raises(ValueError, match="vd_file"):
        export_indicator_pages(tmp_path, mappings_dir=mappings_dir)


# --------------------------------------------------------------------------- #
# The filterable catalogue page
# --------------------------------------------------------------------------- #


def test_catalogue_page_carries_every_series_and_its_facets(
    tmp_path, times_model, mappings_dir
):
    artifacts = export_indicator_pages(
        tmp_path, mappings_dir=mappings_dir, model=times_model
    )
    payload = _catalogue(artifacts["catalogue"].read_text(encoding="utf-8"))
    assert payload["years"] == times_model.years
    assert payload["series"]
    for entry in payload["series"]:
        assert len(entry["values"]) == len(payload["years"])

    # Four facets, each offering exactly the values the series actually take —
    # a filter chip for a value no row has would look like missing data.
    assert [f["key"] for f in payload["facets"]] == [
        "categorie",
        "indicateur",
        "vecteur",
        "technologies",
    ]
    for facet in payload["facets"]:
        assert facet["values"]
        assert set(facet["values"]) == {str(s[facet["key"]]) for s in payload["series"]}


def test_catalogue_page_needs_no_plotly(tmp_path, times_model, mappings_dir):
    """It is a table with inline-SVG sparklines; pulling in the chart library
    for 90 thumbnails would cost 90 Plotly.newPlot calls for no gain."""
    artifacts = export_indicator_pages(
        tmp_path, mappings_dir=mappings_dir, model=times_model
    )
    html = artifacts["catalogue"].read_text(encoding="utf-8")
    assert "cdn.plot.ly" not in html
    assert "timesPypsaSparkline" in html


def test_catalogue_csv_is_tidy(tmp_path, times_model, mappings_dir):
    artifacts = export_indicator_pages(
        tmp_path, mappings_dir=mappings_dir, model=times_model
    )
    path = artifacts["csv_catalogue"]
    header = path.read_text(encoding="utf-8").splitlines()[0]
    assert header == "categorie,indicateur,vecteur,technologies,unite,year,value"
