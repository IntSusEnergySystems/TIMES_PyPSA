"""Standalone HTML pages for the TIMES scenario indicators.

``sankey_pages`` renders *where the energy goes* in one year at a time. This
module renders the other half of the scenario report — the trajectories: final
energy demand, emissions, heat production and the power fleet, each as a stacked
bar per planning horizon with the total drawn on top, next to the table the bar
was built from.

One page per indicator group plus an index, all self-contained (Plotly from the
same CDN as the Sankey pages, no other dependency). Each table is also written
as a CSV beside its page, because the tables are the deliverable as often as the
charts are.
"""

from __future__ import annotations

import html
import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable

import pandas as pd

from times_pypsa.indicators import (
    IndicatorRules,
    IndicatorTable,
    build_indicator_tables,
    catalogue_series,
    indicator_catalogue,
    load_indicator_rules,
)
from times_pypsa.model import TimesAnnualFlows, load_times_annual_flows
from times_pypsa.pipeline import LIBRARY_VERSION, PipelineConfig, default_mappings_dir

logger = logging.getLogger(__name__)

DEFAULT_INDEX_NAME = "times_indicators_index.html"
PAGE_TEMPLATE = "times_indicators_{group}.html"
CSV_TEMPLATE = "times_indicator_{key}.csv"

#: Page groups in report order: (key, French heading, English blurb).
#: `catalogue` is first because it is the entry point — every series on one
#: filterable page — and the other four are the read-through views of it.
GROUPS: tuple[tuple[str, str, str], ...] = (
    (
        "catalogue",
        "Indicateurs [TIMES]",
        "Every TIMES indicator series on one filterable page: pick a category, "
        "an indicator, a carrier or a technology and read the trajectory off "
        "the sparkline. The four pages below are the same numbers, charted. "
        "Every row is a leaf series, so a filtered selection can be summed — "
        "with one caveat: aviation kerosene is a demand series here but is not "
        "part of a sector&rsquo;s final energy (international bunker), so it is "
        "left out of the totals on the demand page.",
    ),
    (
        "demand",
        "Consommation d'énergie [TIMES]",
        "Final energy delivered to each demand sector, by carrier. Metered on the "
        "sector-owned carrier, so heat-pump electricity stays in the electricity "
        "row and EV charging is booked to transport.",
    ),
    (
        "emissions",
        "Émissions de CO2 [TIMES]",
        "Greenhouse gases by emitting sector, and the CO2 that is captured "
        "instead of released.",
    ),
    (
        "heat",
        "Production de chaleur [TIMES]",
        "Useful heat delivered per end use, split by the technology that "
        "delivered it.",
    ),
    (
        "power",
        "Production et capacité électriques [TIMES]",
        "Electricity generated and capacity installed, split between "
        "cogeneration and power-only plant.",
    ),
)

_GROUP_TITLES = {key: title for key, title, _ in GROUPS}

#: The colour cycle the published ClimAct Explorer pages use: Plotly's
#: ``qualitative.Plotly`` for the first ten series of a chart, then
#: ``qualitative.D3`` for the next ten. Read off the legend swatches of the
#: December-2025 screenshots in ``figures_from_demande_haute_04-12-2025/``
#: rather than guessed, so a page of ours can sit beside a published one.
PALETTE: tuple[str, ...] = (
    "#636EFA", "#EF553B", "#00CC96", "#AB63FA", "#FFA15A",
    "#19D3F3", "#FF6692", "#B6E880", "#FF97FF", "#FECB52",
    "#1F77B4", "#FF7F0E", "#2CA02C", "#D62728", "#9467BD",
    "#8C564B", "#E377C2", "#7F7F7F", "#BCBD22", "#17BECF",
)

#: The total line drawn on top of every stacked bar, and the catalogue sparkline.
TOTAL_COLOR = "#FF0000"

#: Preferred colour per row label, per *family* of charts (`color_domain` on
#: `IndicatorTable`).
#:
#: The published pages colour each chart independently — they sort that chart's
#: series alphabetically and walk `PALETTE` — which gives one carrier four
#: colours across four charts. Here the walk is done once per family instead, so
#: a label keeps its colour on every chart that can show it while each family
#: still reproduces its screenshot: the universe of a family is exactly the
#: series set of the published chart it comes from. The sector families are
#: separate for that reason: "Industry" is the second demand sector but the
#: third emitting sector, and both published charts are matched.
#:
#: One label can also mean two things across families — "Electricity" is a
#: carrier on the demand pages and a sector on the emissions page — which is the
#: other reason the map is keyed by family.
SERIES_COLORS: dict[str, dict[str, str]] = {
    # Carriers, alphabetically over `indicators.FUEL_ORDER`. The first fifteen
    # are the industry-demand chart of the screenshots, swatch for swatch; the
    # four carriers that chart has no row for take the tail of the cycle.
    "carrier": {
        "Biofuel": "#636EFA",
        "Biogas": "#EF553B",
        "Black liquor": "#00CC96",
        "Electricity": "#AB63FA",
        "Gas mix": "#FFA15A",
        "Geothermal": "#19D3F3",
        "Heat": "#FF6692",
        "Hydrogen": "#B6E880",
        "LPG": "#FF97FF",
        "Natural Gas Transport": "#FECB52",
        "Oil products": "#1F77B4",
        "Solid fuels": "#FF7F0E",
        "Waste": "#2CA02C",
        "Waste Renewable": "#D62728",
        "Wood": "#9467BD",
        "Derived gas": "#8C564B",
        "Natural Gas": "#E377C2",
        # Grey on purpose: the one row drawn outside the stack (see
        # `indicators.TOTAL_EXCLUDES`).
        "Kerosene": "#7F7F7F",
        "Solar": "#BCBD22",
    },
    # `indicators.DEMAND_SECTORS`, alphabetically.
    "demand_sector": {
        "Agriculture": "#636EFA",
        "Industry": "#EF553B",
        "Residential": "#00CC96",
        "Tertiary": "#AB63FA",
        "Transport": "#FFA15A",
    },
    # `indicators.EMISSION_SECTORS`, alphabetically — a longer universe than the
    # demand one, so the shared names land one slot further along.
    "emission_sector": {
        "Agriculture": "#636EFA",
        "Electricity": "#EF553B",
        "Industry": "#00CC96",
        "Residential": "#AB63FA",
        "Supply": "#FFA15A",
        "Tertiary": "#19D3F3",
        "Transport": "#FF6692",
    },
    # The published heat charts are labelled in French and were walked in French
    # alphabetical order; these are the same swatches against the English
    # labels. `Cogeneration` is the one deliberate departure: the published
    # industry chart has two rows (Chaudière, Cogénération) and gives
    # cogeneration the same red as `Direct electric heating` gets in the
    # residential chart. Ours shows all four rows on the industry chart, so the
    # two cannot share a colour and cogeneration takes the next free one.
    "heat_technology": {
        "Boiler": "#636EFA",
        "Direct electric heating": "#EF553B",
        "Geothermal": "#00CC96",
        "Heat pump": "#AB63FA",
        "District heat": "#FFA15A",
        "Solar thermal": "#19D3F3",
        "Cogeneration": "#FF6692",
    },
}


def indicator_page_name(group: str) -> str:
    return PAGE_TEMPLATE.format(group=group)


def indicator_page_names(
    groups: Iterable[str] | None = None,
    *,
    index_name: str | None = DEFAULT_INDEX_NAME,
) -> list[str]:
    """Every HTML file :func:`export_indicator_pages` writes, without reading the .vd.

    A Snakemake rule has to declare its outputs before the data exists, so the
    file list has exactly one definition and cannot drift from what is written.
    """
    keys = list(groups) if groups is not None else [g for g, _, _ in GROUPS]
    names = [indicator_page_name(g) for g in keys]
    if index_name:
        names.append(index_name)
    return names


def _assign_colors(labels: list[str], domain: str = "technology") -> dict[str, str]:
    """Preferred colour per label, with collisions inside one chart resolved.

    ``SERIES_COLORS[domain]`` gives the label its colour across every chart of
    its family; a label the family does not name — a power technology, a carrier
    a rule table gained after this map was written — takes the first unused
    ``PALETTE`` colour, so a chart never draws two bands the same colour even
    when the map does not reach.
    """
    preferred = SERIES_COLORS.get(domain, {})
    used: set[str] = set()
    out: dict[str, str] = {}
    for label in labels:
        color = preferred.get(label)
        if color is None or color in used:
            color = next(
                (c for c in PALETTE if c not in used),
                PALETTE[len(out) % len(PALETTE)],
            )
        used.add(color)
        out[label] = color
    return out


def _chart_payload(table: IndicatorTable) -> dict:
    """Chart JSON for one table.

    ``excluded`` marks a row reported by the table but left out of its total —
    aviation kerosene, an international bunker. Those rows are drawn beside the
    stack rather than in it, so the stacked height keeps equalling the total line
    drawn on top of it; a bar that is visibly taller than its own total reads as
    an extraction bug.
    """
    years = table.years
    excluded = set(table.excluded_rows)
    labels = [str(row) for row in table.frame.index]
    colors = _assign_colors(labels, table.color_domain)
    traces = [
        {
            "name": label,
            "values": [float(v) for v in table.frame.loc[row].tolist()],
            "color": colors[label],
            "excluded": row in excluded,
        }
        for label, row in zip(labels, table.frame.index)
    ]
    total = (
        [float(v) for v in table.total.reindex(table.frame.columns).tolist()]
        if table.total is not None
        else None
    )
    return {
        "id": table.key,
        "title": table.title,
        "unit": table.unit,
        "years": years,
        "traces": traces,
        "total": total,
        "rowLabel": table.row_label,
    }


def _table_html(table: IndicatorTable) -> str:
    frame = table.to_csv_frame()
    excluded = set(table.excluded_rows)
    head = "".join(f"<th>{y}</th>" for y in table.frame.columns)
    rows = []
    for label, values in frame.iterrows():
        cells = "".join(f"<td>{v:,.2f}</td>" for v in values)
        cls = " class='total-row'" if label == "Total" else ""
        text = html.escape(str(label))
        if label in excluded:
            cls = " class='excluded-row'"
            text += " <sup>*</sup>"
        rows.append(f"<tr{cls}><th>{text}</th>{cells}</tr>")
    note = (
        "\n<p class='table-note'>* reported but not part of the total, and drawn "
        "beside the stack rather than in it.</p>"
        if excluded
        else ""
    )
    return (
        f"<table class='indicator-table'>\n"
        f"<tr><th>{html.escape(table.row_label)} [{html.escape(table.unit)}]</th>{head}</tr>\n"
        + "\n".join(rows)
        + "\n</table>"
        + note
    )


_INTERACTIVE_JS = """
function timesPypsaIndicatorPlot(chart) {
  const el = document.getElementById("plot-" + chart.id);
  if (!el || typeof Plotly === "undefined") return;
  // Rows that are not part of the total (aviation kerosene, an international
  // bunker) are drawn as a narrow bar beside the stack instead of in it, so the
  // stacked height keeps equalling the total line drawn on top of it.
  //
  // `base` is what takes them out of the stack: Plotly.js draws a bar that sets
  // it in overlay mode, and it is the only lever that does. `offsetgroup` looks
  // like the right key but is honoured in `barmode: "group"` only — under
  // `relative` the bar lands back on top of the stack, narrower.
  const split = chart.traces.some(function (t) { return t.excluded; });
  const asideBase = chart.years.map(function () { return 0; });
  const traces = chart.traces.map(function (t) {
    const trace = {
      type: "bar",
      name: t.excluded ? t.name + " (hors total)" : t.name,
      x: chart.years,
      y: t.values,
      marker: {
        color: t.color,
        pattern: t.excluded ? { shape: "/", size: 4, solidity: 0.35 } : undefined,
      },
      hovertemplate: "%{fullData.name}<br>%{x}: %{y:,.1f} " + chart.unit + "<extra></extra>",
    };
    if (split && t.excluded) {
      // Stack the excluded rows among themselves, from zero, to the right of
      // the tick. Today there is one; two would otherwise overdraw each other.
      trace.base = asideBase.slice();
      trace.width = 0.22;
      trace.offset = 0.27;
      t.values.forEach(function (v, i) { asideBase[i] += v; });
    } else if (split) {
      // The stack stays centred on the tick so the total line still lands on it.
      trace.width = 0.5;
      trace.offset = -0.25;
    }
    return trace;
  });
  if (chart.total) {
    traces.push({
      type: "scatter",
      mode: "lines+markers",
      name: "Total",
      x: chart.years,
      y: chart.total,
      line: { color: TIMES_PYPSA_TOTAL_COLOR, width: 2 },
      marker: { size: 6 },
      hovertemplate: "Total<br>%{x}: %{y:,.1f} " + chart.unit + "<extra></extra>",
    });
  }
  Plotly.newPlot(el, traces, {
    barmode: "relative",
    margin: { l: 70, r: 20, t: 10, b: 60 },
    xaxis: { title: "Année", type: "category" },
    yaxis: { title: chart.unit, zeroline: true },
    // Legend under the axis, not beside it: these pages are read in a report
    // column and a right-hand legend is the first thing a narrow viewport clips.
    legend: { orientation: "h", x: 0, y: -0.18, yanchor: "top" },
    hovermode: "closest",
    height: 480,
  }, { responsive: true, displaylogo: false });
}

function timesPypsaInitIndicators(charts) {
  charts.forEach(timesPypsaIndicatorPlot);
}
"""

_STYLE = """
    body { font-family: system-ui, sans-serif; margin: 24px; max-width: 1500px; }
    h1, h2 { color: #222; }
    h2 { margin-top: 40px; border-bottom: 1px solid #e0e0e0; padding-bottom: 4px; }
    .meta { color: #555; }
    .block { display: flex; flex-wrap: wrap; gap: 24px; align-items: flex-start; }
    .block > .plot { flex: 1 1 620px; min-width: 480px; }
    .block > .grid { flex: 1 1 480px; overflow-x: auto; }
    table.indicator-table { border-collapse: collapse; font-size: 13px; }
    table.indicator-table th, table.indicator-table td {
      border: 1px solid #ccc; padding: 3px 8px; text-align: right;
    }
    table.indicator-table th:first-child { text-align: left; }
    table.indicator-table tr:first-child th { background: #f0f0f0; text-align: center; }
    table.indicator-table tr.total-row { background: #f7f7f7; font-weight: 600; }
    table.indicator-table tr.excluded-row th, table.indicator-table tr.excluded-row td {
      color: #666; font-style: italic;
    }
    .table-note { font-size: 12px; color: #666; margin: 4px 0 0; }
    table.nav-table { border-collapse: collapse; margin: 16px 0; }
    table.nav-table th, table.nav-table td { border: 1px solid #ccc; padding: 6px 12px; }
    .caveat {
      padding: 12px 16px; background: #fff8e8;
      border-left: 4px solid #d68910; margin: 16px 0;
    }
"""


_CATALOGUE_JS = r"""
function timesPypsaSparkline(values, w, h) {
  const pts = values.filter(function (v) { return v !== null; });
  if (pts.length < 2) return "";
  let lo = Math.min.apply(null, pts), hi = Math.max.apply(null, pts);
  if (lo > 0) lo = 0;                       // bars grow from zero; so does the line
  if (hi === lo) { hi = lo + 1; }
  const n = values.length;
  const x = function (i) { return (i / (n - 1)) * (w - 2) + 1; };
  const y = function (v) { return h - 1 - ((v - lo) / (hi - lo)) * (h - 2); };
  let d = "", started = false;
  values.forEach(function (v, i) {
    if (v === null) return;
    d += (started ? " L" : "M") + x(i).toFixed(1) + "," + y(v).toFixed(1);
    started = true;
  });
  const zero = lo < 0 && hi > 0
    ? "<line x1='1' y1='" + y(0).toFixed(1) + "' x2='" + (w - 1) + "' y2='" +
      y(0).toFixed(1) + "' stroke='#ccc' stroke-width='1'/>"
    : "";
  return "<svg class='spark' width='" + w + "' height='" + h + "' viewBox='0 0 " + w +
         " " + h + "' aria-hidden='true'>" + zero +
         "<path d='" + d + "' fill='none' stroke='" + TIMES_PYPSA_TOTAL_COLOR +
         "' stroke-width='1.5'/></svg>";
}

function timesPypsaFormat(v) {
  if (v === null || v === undefined) return "";
  const a = Math.abs(v);
  const digits = a >= 1000 ? 0 : a >= 10 ? 1 : a >= 1 ? 2 : 3;
  return v.toLocaleString("en-GB", {
    minimumFractionDigits: digits, maximumFractionDigits: digits,
  });
}

function timesPypsaDelta(values) {
  const first = values.find(function (v) { return v !== null; });
  let last = null;
  for (let i = values.length - 1; i >= 0; i--) {
    if (values[i] !== null) { last = values[i]; break; }
  }
  if (first === undefined || last === null) return { text: "—", value: null };
  if (first === 0) return last === 0
    ? { text: "—", value: null }
    : { text: "∞", value: Infinity };
  const pct = ((last - first) / Math.abs(first)) * 100;
  return {
    text: (pct >= 0 ? "+" : "") + pct.toFixed(1) + "%",
    value: pct,
  };
}

function timesPypsaInitCatalogue(payload) {
  const root = document.getElementById("catalogue");
  if (!root) return;
  const years = payload.years;
  const series = payload.series;
  const facets = payload.facets;
  const selected = {};
  facets.forEach(function (f) { selected[f.key] = new Set(f.values); });
  let search = "";
  let sortCol = null, sortDir = 1;

  const chipBox = root.querySelector(".filters");
  facets.forEach(function (f) {
    const group = document.createElement("div");
    group.className = "facet";
    const head = document.createElement("div");
    head.className = "facet-head";
    head.innerHTML = "<strong>" + f.label + "</strong> " +
      "<a href='#' data-all='1'>tout</a> · <a href='#' data-none='1'>aucun</a>";
    group.appendChild(head);
    const list = document.createElement("div");
    list.className = "chips";
    f.values.forEach(function (v) {
      const chip = document.createElement("button");
      chip.type = "button";
      chip.className = "chip on";
      chip.textContent = v;
      chip.addEventListener("click", function () {
        if (selected[f.key].has(v)) { selected[f.key].delete(v); chip.classList.remove("on"); }
        else { selected[f.key].add(v); chip.classList.add("on"); }
        render();
      });
      list.appendChild(chip);
    });
    head.querySelector("[data-all]").addEventListener("click", function (e) {
      e.preventDefault();
      f.values.forEach(function (v) { selected[f.key].add(v); });
      list.querySelectorAll(".chip").forEach(function (c) { c.classList.add("on"); });
      render();
    });
    head.querySelector("[data-none]").addEventListener("click", function (e) {
      e.preventDefault();
      selected[f.key].clear();
      list.querySelectorAll(".chip").forEach(function (c) { c.classList.remove("on"); });
      render();
    });
    group.appendChild(list);
    chipBox.appendChild(group);
  });

  const box = root.querySelector(".search input");
  box.addEventListener("input", function () {
    search = box.value.trim().toLowerCase();
    render();
  });

  function matches(s) {
    for (let i = 0; i < facets.length; i++) {
      const k = facets[i].key;
      if (!selected[k].has(s[k])) return false;
    }
    if (!search) return true;
    return facets.some(function (f) {
      return String(s[f.key]).toLowerCase().indexOf(search) >= 0;
    });
  }

  function render() {
    const rows = series.filter(matches);
    if (sortCol !== null) {
      rows.sort(function (a, b) {
        let av, bv;
        if (typeof sortCol === "number") { av = a.values[sortCol]; bv = b.values[sortCol]; }
        else if (sortCol === "delta") { av = timesPypsaDelta(a.values).value; bv = timesPypsaDelta(b.values).value; }
        else { av = a[sortCol]; bv = b[sortCol]; }
        if (av === null || av === undefined) av = -Infinity;
        if (bv === null || bv === undefined) bv = -Infinity;
        if (typeof av === "string") return sortDir * av.localeCompare(bv);
        return sortDir * (av - bv);
      });
    }
    const body = rows.map(function (s) {
      const d = timesPypsaDelta(s.values);
      return "<tr>" +
        "<td>" + s.categorie + "</td>" +
        "<td>" + s.indicateur + "</td>" +
        "<td>" + s.vecteur + "</td>" +
        "<td>" + s.technologies + "</td>" +
        "<td class='unit'>" + s.unite + "</td>" +
        "<td class='sparkcell'>" + timesPypsaSparkline(s.values, 130, 26) + "</td>" +
        s.values.map(function (v) { return "<td class='num'>" + timesPypsaFormat(v) + "</td>"; }).join("") +
        "<td class='num delta'>" + d.text + "</td>" +
        "</tr>";
    }).join("");
    root.querySelector("tbody").innerHTML = body ||
      "<tr><td colspan='" + (7 + years.length) + "' class='empty'>Aucune série ne " +
      "correspond aux filtres.</td></tr>";
    root.querySelector(".count").textContent =
      rows.length + " / " + series.length + " séries";
  }

  root.querySelectorAll("thead th[data-sort]").forEach(function (th) {
    th.addEventListener("click", function () {
      const key = th.dataset.sort;
      const col = /^\d+$/.test(key) ? parseInt(key, 10) : key;
      if (sortCol === col) { sortDir = -sortDir; } else { sortCol = col; sortDir = 1; }
      root.querySelectorAll("thead th[data-sort]").forEach(function (o) {
        o.classList.remove("asc", "desc");
      });
      th.classList.add(sortDir > 0 ? "asc" : "desc");
      render();
    });
  });

  render();
}
"""

_CATALOGUE_STYLE = """
    .filters {
      display: flex; flex-wrap: wrap; gap: 18px 32px;
      padding: 14px 16px; background: #fafafa; border: 1px solid #e0e0e0;
      border-radius: 6px; margin: 12px 0;
    }
    .facet { flex: 1 1 260px; min-width: 240px; }
    .facet-head { font-size: 13px; margin-bottom: 6px; color: #333; }
    .facet-head a { color: #0b57d0; text-decoration: none; font-size: 12px; }
    .chips { display: flex; flex-wrap: wrap; gap: 4px; }
    .chip {
      font: inherit; font-size: 12px; line-height: 1.3; cursor: pointer;
      border: 1px solid #bbb; background: #fff; color: #666;
      border-radius: 12px; padding: 2px 10px;
    }
    .chip.on { background: #16a37f; border-color: #16a37f; color: #fff; }
    .search { margin: 12px 0; }
    .search input {
      font: inherit; padding: 6px 10px; width: min(420px, 100%);
      border: 1px solid #bbb; border-radius: 4px;
    }
    .count { color: #555; font-size: 13px; margin-left: 12px; }
    .catalogue-wrap { overflow-x: auto; }
    table.catalogue { border-collapse: collapse; font-size: 12.5px; width: 100%; }
    table.catalogue th, table.catalogue td {
      border: 1px solid #ddd; padding: 3px 8px; vertical-align: middle;
    }
    table.catalogue thead th {
      background: #f0f0f0; position: sticky; top: 0; text-align: left;
      white-space: nowrap;
    }
    table.catalogue thead th[data-sort] { cursor: pointer; user-select: none; }
    table.catalogue thead th.asc::after { content: " \\2191"; }
    table.catalogue thead th.desc::after { content: " \\2193"; }
    table.catalogue td.num { text-align: right; white-space: nowrap; }
    table.catalogue td.unit { color: #555; white-space: nowrap; }
    table.catalogue td.delta { font-weight: 600; }
    table.catalogue td.sparkcell { padding: 0 6px; width: 142px; }
    table.catalogue td.empty { text-align: center; color: #777; padding: 18px; }
    svg.spark { display: block; }
"""


def _catalogue_payload(catalogue: pd.DataFrame) -> dict:
    years, series = catalogue_series(catalogue)
    facets = [
        {"key": key, "label": label, "values": sorted({str(s[key]) for s in series})}
        for key, label in (
            ("categorie", "Catégories"),
            ("indicateur", "Indicateurs"),
            ("vecteur", "Vecteurs"),
            ("technologies", "Technologies"),
        )
    ]
    return {"years": years, "series": series, "facets": facets}


def _catalogue_body(payload: dict) -> str:
    years = payload["years"]
    head = "".join(
        f"<th data-sort='{i}'>{y}</th>" for i, y in enumerate(years)
    )
    return f"""
<div id="catalogue">
  <div class="filters"></div>
  <div class="search">
    <input type="search" placeholder="Filtrer par mot-clé (secteur, vecteur, technologie…)"
           aria-label="Filtrer">
    <span class="count"></span>
  </div>
  <div class="catalogue-wrap">
  <table class="catalogue">
    <thead><tr>
      <th data-sort="categorie">Catégorie</th>
      <th data-sort="indicateur">Indicateur</th>
      <th data-sort="vecteur">Vecteur</th>
      <th data-sort="technologies">Technologies</th>
      <th data-sort="unite">Unité</th>
      <th>Évolution {years[0] if years else ''}&rarr;{years[-1] if years else ''}</th>
      {head}
      <th data-sort="delta">&Delta;%</th>
    </tr></thead>
    <tbody></tbody>
  </table>
  </div>
</div>
"""


def _catalogue_page_html(
    *,
    catalogue: pd.DataFrame,
    groups: list[str],
    index_name: str | None,
    scenario_label: str,
    vd_name: str,
    heading: str,
    blurb: str,
) -> str:
    scenario_bit = f" — {html.escape(scenario_label)}" if scenario_label else ""
    if catalogue.empty:
        body = (
            "<p class='caveat'>No indicator series in "
            f"<code>{html.escape(vd_name)}</code>.</p>"
        )
        script = ""
    else:
        payload = _catalogue_payload(catalogue)
        body = _catalogue_body(payload)
        script = (
            "<script>\n"
            f"const TIMES_PYPSA_TOTAL_COLOR = {json.dumps(TOTAL_COLOR)};\n"
            f"const TIMES_PYPSA_CATALOGUE = {json.dumps(payload, ensure_ascii=False)};\n"
            f"{_CATALOGUE_JS}\n"
            'document.addEventListener("DOMContentLoaded", function () {\n'
            "  timesPypsaInitCatalogue(TIMES_PYPSA_CATALOGUE);\n"
            "});\n</script>"
        )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(heading)}</title>
  <style>{_STYLE}{_CATALOGUE_STYLE}</style>
</head>
<body>
<h1>{html.escape(heading)}{scenario_bit}</h1>
{_nav_html("catalogue", groups, index_name)}
<p class="meta">{blurb}</p>
{body}
{_footer(vd_name, scenario_label)}
{script}
</body>
</html>
"""


def _nav_html(group: str, groups: list[str], index_name: str | None) -> str:
    parts = []
    if index_name:
        parts.append(f"<a href='{html.escape(index_name)}'>All indicators</a>")
    links = " · ".join(
        f"<strong>{html.escape(_GROUP_TITLES[g])}</strong>"
        if g == group
        else f"<a href='{html.escape(indicator_page_name(g))}'>{html.escape(_GROUP_TITLES[g])}</a>"
        for g in groups
    )
    parts.append(links)
    return f"<p class='meta'>{' &nbsp;|&nbsp; '.join(parts)}</p>"


def _footer(vd_name: str, scenario_label: str) -> str:
    stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d %H:%M UTC")
    scen = f"scenario <code>{html.escape(scenario_label)}</code> · " if scenario_label else ""
    return (
        "<hr><p class='meta'>"
        f"TIMES source: <code>{html.escape(vd_name)}</code> · {scen}"
        f"times_pypsa {LIBRARY_VERSION} · generated {stamp}<br>"
        "Definitions and the reconciliation against the December-2025 ICEDD "
        "figures: <code>INDICATORS.md</code> in the TIMES_PyPSA repository."
        "</p>"
    )


def _page_html(
    *,
    group: str,
    heading: str,
    blurb: str,
    tables: list[IndicatorTable],
    groups: list[str],
    index_name: str | None,
    scenario_label: str,
    vd_name: str,
) -> str:
    charts = [_chart_payload(t) for t in tables]
    sections = []
    for table in tables:
        sections.append(
            f"<h2 id='{html.escape(table.key)}'>{html.escape(table.title)}</h2>\n"
            f"<p class='meta'>{table.subtitle}</p>\n"
            "<div class='block'>\n"
            f"  <div class='plot'><div id='plot-{html.escape(table.key)}'></div></div>\n"
            f"  <div class='grid'>{_table_html(table)}</div>\n"
            "</div>"
        )
    scenario_bit = f" — {html.escape(scenario_label)}" if scenario_label else ""
    body = "\n".join(sections) if sections else (
        "<p class='caveat'>No data for this indicator group in "
        f"<code>{html.escape(vd_name)}</code>.</p>"
    )
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{html.escape(heading)}</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>{_STYLE}</style>
</head>
<body>
<h1>{html.escape(heading)}{scenario_bit}</h1>
{_nav_html(group, groups, index_name)}
<p class="meta">{blurb}</p>
{body}
{_footer(vd_name, scenario_label)}
<script>
const TIMES_PYPSA_TOTAL_COLOR = {json.dumps(TOTAL_COLOR)};
const TIMES_PYPSA_INDICATORS = {json.dumps(charts, ensure_ascii=False)};
{_INTERACTIVE_JS}
document.addEventListener("DOMContentLoaded", function () {{
  timesPypsaInitIndicators(TIMES_PYPSA_INDICATORS);
}});
</script>
</body>
</html>
"""


def _index_html(
    *,
    groups: list[str],
    tables_by_group: dict[str, list[IndicatorTable]],
    scenario_label: str,
    vd_name: str,
) -> str:
    rows = []
    for key, title, blurb in GROUPS:
        if key not in groups:
            continue
        if key == "catalogue":
            items = "one filterable row per series, with a sparkline and Δ%"
        else:
            items = ", ".join(html.escape(t.title) for t in tables_by_group.get(key, []))
        rows.append(
            f"<tr><td><a href='{html.escape(indicator_page_name(key))}'>"
            f"{html.escape(title)}</a></td><td>{blurb}</td>"
            f"<td class='meta'>{items or '—'}</td></tr>"
        )
    scenario_bit = f" — {html.escape(scenario_label)}" if scenario_label else ""
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>TIMES scenario indicators — index</title>
  <style>{_STYLE}</style>
</head>
<body>
<h1>TIMES scenario indicators{scenario_bit}</h1>
<p class="meta">
  Trajectory charts and tables read directly from the TIMES <code>.vd</code>,
  without passing through PyPSA. Each page shows one stacked bar per planning
  horizon with the total on top, next to the table it was built from; the same
  tables are written as CSV beside the pages.
</p>
<table class="nav-table">
  <tr><th>Page</th><th>What it shows</th><th>Charts</th></tr>
  {''.join(rows)}
</table>
{_footer(vd_name, scenario_label)}
</body>
</html>
"""


def export_indicator_pages(
    out_dir: Path | str,
    *,
    vd_file: Path | str | None = None,
    mappings_dir: Path | str | None = None,
    years: Iterable[int] | None = None,
    scenario_label: str = "",
    write_index: bool = True,
    index_name: str | None = DEFAULT_INDEX_NAME,
    write_csv: bool = True,
    config: PipelineConfig | None = None,
    model: TimesAnnualFlows | None = None,
    rules: IndicatorRules | None = None,
) -> dict[str, Path]:
    """Write one indicator page per group into ``out_dir``, plus an index.

    Pass ``model`` to reuse a ``.vd`` already parsed (the Sankey pages and these
    read the same file); otherwise ``vd_file`` is parsed once here.

    Every declared page is written even when its group has no data, so a caller
    that fixed its output list up front — a Snakemake rule, a report manifest —
    does not fail on a missing file; the page says the group was empty.

    Returns ``{artifact_key: path}`` with a key per group, ``"index"`` and, when
    ``write_csv``, ``"csv_<table key>"``.
    """
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    requested_years = [int(y) for y in years] if years is not None else None
    if model is None:
        if vd_file is None:
            raise ValueError("Pass either `vd_file` or an already-loaded `model`")
        if config is None:
            config = PipelineConfig(
                start_year=min(requested_years)
                if requested_years
                else PipelineConfig().start_year
            )
        mappings_dir = Path(mappings_dir or default_mappings_dir())
        model = load_times_annual_flows(vd_file, mappings_dir, config=config)
    else:
        mappings_dir = Path(mappings_dir or model.mappings_dir)

    rules = rules or load_indicator_rules(mappings_dir)
    vd_name = Path(vd_file).name if vd_file is not None else "(preloaded model)"

    if requested_years is not None:
        missing = sorted(set(requested_years) - set(model.years))
        if missing:
            logger.warning(
                "Years %s are not in %s (model has %s); their bars will be absent.",
                missing,
                vd_name,
                model.years,
            )

    tables = build_indicator_tables(model, rules, years=requested_years)
    by_group: dict[str, list[IndicatorTable]] = {key: [] for key, _, _ in GROUPS}
    for table in tables:
        by_group.setdefault(table.group, []).append(table)

    groups = [key for key, _, _ in GROUPS]
    artifacts: dict[str, Path] = {}
    catalogue = indicator_catalogue(model, rules, tables=tables)

    for key, heading, blurb in GROUPS:
        page = out_dir / indicator_page_name(key)

        if key == "catalogue":
            if catalogue.empty:
                logger.warning("Indicator catalogue is empty for %s.", vd_name)
            page.write_text(
                _catalogue_page_html(
                    catalogue=catalogue,
                    groups=groups,
                    index_name=index_name if write_index else None,
                    scenario_label=scenario_label,
                    vd_name=vd_name,
                    heading=heading,
                    blurb=blurb,
                ),
                encoding="utf-8",
            )
            artifacts[key] = page
            logger.info("Wrote %s (%d series)", page, catalogue_series(catalogue)[1].__len__())
            if write_csv and not catalogue.empty:
                path = out_dir / CSV_TEMPLATE.format(key="catalogue")
                catalogue.to_csv(path, index=False, float_format="%.6g")
                artifacts["csv_catalogue"] = path
            continue

        group_tables = by_group.get(key, [])
        if not group_tables:
            logger.warning("Indicator group %r is empty for %s.", key, vd_name)
        page.write_text(
            _page_html(
                group=key,
                heading=heading,
                blurb=blurb,
                tables=group_tables,
                groups=groups,
                index_name=index_name if write_index else None,
                scenario_label=scenario_label,
                vd_name=vd_name,
            ),
            encoding="utf-8",
        )
        artifacts[key] = page
        logger.info("Wrote %s", page)

        if write_csv:
            for table in group_tables:
                path = out_dir / CSV_TEMPLATE.format(key=table.key)
                frame = table.to_csv_frame()
                frame.index.name = table.row_label
                frame.to_csv(path, float_format="%.6g")
                artifacts[f"csv_{table.key}"] = path

    if write_index and index_name:
        index = out_dir / index_name
        index.write_text(
            _index_html(
                groups=groups,
                tables_by_group=by_group,
                scenario_label=scenario_label,
                vd_name=vd_name,
            ),
            encoding="utf-8",
        )
        artifacts["index"] = index
        logger.info("Wrote %s", index)

    return artifacts


def indicator_frames(
    model: TimesAnnualFlows,
    rules: IndicatorRules | None = None,
    *,
    years: Iterable[int] | None = None,
) -> dict[str, pd.DataFrame]:
    """``{table key: wide frame with the Total row}`` — the tables, without HTML."""
    requested = [int(y) for y in years] if years is not None else None
    tables = build_indicator_tables(model, rules or load_indicator_rules(), years=requested)
    return {t.key: t.to_csv_frame() for t in tables}
