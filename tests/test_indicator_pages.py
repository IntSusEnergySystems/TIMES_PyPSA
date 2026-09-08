"""HTML indicator pages written into a report folder."""

from __future__ import annotations

import json
import re

import pytest

from times_pypsa.indicator_pages import (
    DEFAULT_INDEX_NAME,
    GROUPS,
    PALETTE,
    SERIES_COLORS,
    TOTAL_COLOR,
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


def test_stacked_traces_add_up_to_the_total_line(tmp_path, times_model, mappings_dir):
    """A bar taller than the total line drawn on it reads as an extraction bug.

    The transport chart was exactly that: aviation kerosene is reported but is
    not part of the sector total, and it was stacked anyway. Rows outside the
    total now carry `excluded` and are drawn beside the stack, so what stacks is
    what the line says.
    """
    artifacts = export_indicator_pages(
        tmp_path, mappings_dir=mappings_dir, model=times_model
    )
    checked = 0
    for key, _, _ in GROUPS:
        if key == "catalogue":
            continue
        for chart in _charts(artifacts[key].read_text(encoding="utf-8")):
            if chart["total"] is None:
                continue
            stacked = [t for t in chart["traces"] if not t["excluded"]]
            assert stacked, chart["id"]
            for i, total in enumerate(chart["total"]):
                assert sum(t["values"][i] for t in stacked) == pytest.approx(
                    total, rel=1e-9, abs=1e-6
                ), f"{chart['id']} {chart['years'][i]}"
            checked += 1
    assert checked > 0


def test_kerosene_is_drawn_outside_the_transport_stack(
    tmp_path, times_model, mappings_dir
):
    """The one series the report is allowed to draw outside a stack."""
    artifacts = export_indicator_pages(
        tmp_path, mappings_dir=mappings_dir, model=times_model
    )
    page = artifacts["demand"].read_text(encoding="utf-8")
    charts = {c["id"]: c for c in _charts(page)}
    tra = charts.get("demand_tra")
    if tra is None or not any(t["name"] == "Kerosene" for t in tra["traces"]):
        pytest.skip("no aviation kerosene in this .vd")
    assert [t["name"] for t in tra["traces"] if t["excluded"]] == ["Kerosene"]
    # `base` is what takes the bar out of the stack — see the note in the JS.
    assert "trace.base = asideBase.slice();" in page
    assert "hors total" in page
    # And the table says why the row does not add up with the others.
    assert "reported but not part of the total" in page


def test_colors_are_unique_within_one_chart():
    """`Electricity` is a carrier on one page and a sector on another; without
    the collision fix it shares Industry's blue and the bands merge."""
    labels = ["Agriculture", "Electricity", "Industry", "Residential", "Tertiary"]
    colors = _assign_colors(labels, "emission_sector")
    assert len(set(colors.values())) == len(labels)


def test_every_series_colour_comes_from_the_reference_palette():
    """The published pages' cycle, sampled from the December-2025 screenshots.

    Charts of ours are read beside theirs; a colour from outside the cycle is
    the thing that makes the pair look like two different reports.
    """
    for domain, colors in SERIES_COLORS.items():
        unknown = sorted(set(colors.values()) - set(PALETTE))
        assert unknown == [], f"{domain}: {unknown}"
        # Within one family a label must be one colour and a colour one label,
        # or the collision resolver silently repaints a series per chart.
        assert len(set(colors.values())) == len(colors), domain
    assert TOTAL_COLOR in {"#FF0000"}


def test_published_charts_keep_their_published_swatches():
    """The three series sets the screenshots pin exactly, swatch for swatch."""
    assert _assign_colors(
        ["Agriculture", "Industry", "Residential", "Tertiary", "Transport"],
        "demand_sector",
    ) == {
        "Agriculture": "#636EFA",
        "Industry": "#EF553B",
        "Residential": "#00CC96",
        "Tertiary": "#AB63FA",
        "Transport": "#FFA15A",
    }
    assert _assign_colors(
        ["Agriculture", "Electricity", "Industry", "Residential", "Supply",
         "Tertiary", "Transport"],
        "emission_sector",
    ) == {
        "Agriculture": "#636EFA",
        "Electricity": "#EF553B",
        "Industry": "#00CC96",
        "Residential": "#AB63FA",
        "Supply": "#FFA15A",
        "Tertiary": "#19D3F3",
        "Transport": "#FF6692",
    }
    assert _assign_colors(
        ["Boiler", "Direct electric heating", "Geothermal", "Heat pump",
         "District heat", "Solar thermal"],
        "heat_technology",
    ) == {
        "Boiler": "#636EFA",
        "Direct electric heating": "#EF553B",
        "Geothermal": "#00CC96",
        "Heat pump": "#AB63FA",
        "District heat": "#FFA15A",
        "Solar thermal": "#19D3F3",
    }


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
