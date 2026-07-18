"""Interactive multi-year Sankey HTML (standalone, not SEPIA)."""

from __future__ import annotations

import colorsys
import hashlib
import json
from typing import Any

import pandas as pd

from times_pypsa.units import EnergyUnit, pj_to_display, unit_label

EXPORTED_COLOR = "rgba(31, 119, 180, 0.85)"
CONTEXT_COLOR = "rgba(160, 160, 160, 0.45)"
MIXED_COLOR = "rgba(255, 152, 152, 0.75)"

# Process nodes → blue family; commodity nodes → amber family (Plotly has no shapes).
_PROCESS_HSV = (205.0, 0.55, 0.72)
_COMMODITY_HSV = (32.0, 0.70, 0.88)


def export_status_color(export_status: str) -> str:
    if export_status == "exported":
        return EXPORTED_COLOR
    if export_status == "mixed":
        return MIXED_COLOR
    return CONTEXT_COLOR


def link_export_status(row: pd.Series) -> str:
    status = str(row.get("export_status", "") or "").strip()
    if status in {"exported", "context", "mixed"}:
        return status
    if bool(row.get("exported", False)):
        return "exported"
    return "context"


def export_status_hover(export_status: str, cats: str) -> str:
    return {
        "exported": "<br>Exported to pypsa-wal",
        "mixed": "<br>Mixed link (exported and non-exported flows aggregated)",
        "context": "<br>Not exported to pypsa-wal",
    }.get(export_status, "")


def node_key(kind: str, label: str) -> str:
    """Unique id so a process and commodity sharing a name stay distinct nodes."""
    kind_n = "process" if str(kind).lower().startswith("p") else "commodity"
    return f"{kind_n}::{label}"


def node_display_label(kind: str, label: str) -> str:
    kind_n = "process" if str(kind).lower().startswith("p") else "commodity"
    marker = "P" if kind_n == "process" else "C"
    return f"{marker} · {label}"


def node_kind_color(kind: str, label: str) -> str:
    """Vary colour within the process (blue) or commodity (amber) hue family."""
    kind_n = "process" if str(kind).lower().startswith("p") else "commodity"
    base_h, base_s, base_v = _PROCESS_HSV if kind_n == "process" else _COMMODITY_HSV
    digest = hashlib.md5(f"{kind_n}:{label}".encode()).hexdigest()
    hue_jitter = (int(digest[:2], 16) / 255.0 - 0.5) * 24.0
    val_jitter = (int(digest[2:4], 16) / 255.0 - 0.5) * 0.18
    h = ((base_h + hue_jitter) % 360.0) / 360.0
    s = min(0.85, max(0.35, base_s + (int(digest[4:6], 16) / 255.0 - 0.5) * 0.15))
    v = min(0.95, max(0.45, base_v + val_jitter))
    r, g, b = colorsys.hsv_to_rgb(h, s, v)
    return f"rgba({int(r * 255)}, {int(g * 255)}, {int(b * 255)}, 0.92)"


def collect_typed_nodes(links: pd.DataFrame) -> list[dict[str, str]]:
    """Ordered unique typed nodes from link endpoints (needs source_kind/target_kind)."""
    if links.empty:
        return []
    ordered: list[dict[str, str]] = []
    seen: set[str] = set()
    has_kinds = {"source_kind", "target_kind"}.issubset(links.columns)
    if not has_kinds:
        for name in pd.concat([links["source"], links["target"]]).astype(str).unique():
            ordered.append(
                {
                    "key": name,
                    "label": name,
                    "kind": "unknown",
                    "raw": name,
                    "color": "rgba(120,120,120,0.85)",
                }
            )
        return ordered

    for _, row in links.iterrows():
        for kind_col, name_col in (("source_kind", "source"), ("target_kind", "target")):
            label = str(row[name_col])
            kind = str(row.get(kind_col) or "").strip().lower()
            if kind not in {"process", "commodity"}:
                continue
            key = node_key(kind, label)
            if key in seen:
                continue
            seen.add(key)
            ordered.append(
                {
                    "key": key,
                    "label": node_display_label(kind, label),
                    "kind": kind,
                    "raw": label,
                    "color": node_kind_color(kind, label),
                }
            )
    return ordered


def links_to_records(
    links: pd.DataFrame,
    *,
    units: EnergyUnit = "twh",
) -> list[dict[str, Any]]:
    """Serialize Sankey links for client-side Plotly rendering."""
    if links.empty:
        return []

    label = unit_label(units)
    has_kinds = {"source_kind", "target_kind"}.issubset(links.columns)
    records: list[dict[str, Any]] = []
    for _, row in links.iterrows():
        v_pj = float(row["value"])
        v = pj_to_display(v_pj, units)
        cats = str(row.get("matched_categories", "") or "")
        status = link_export_status(row)
        color = export_status_color(status)

        src = str(row["source"])
        tgt = str(row["target"])
        if has_kinds:
            sk = str(row["source_kind"])
            tk = str(row["target_kind"])
            source_key = node_key(sk, src)
            target_key = node_key(tk, tgt)
            tip = (
                f"{node_display_label(sk, src)} → {node_display_label(tk, tgt)}"
                f"<br>{v:.2f} {label}"
            )
        else:
            source_key = src
            target_key = tgt
            tip = f"{src} → {tgt}<br>{v:.2f} {label}"
        if cats:
            tip += f"<br>Categories: {cats.replace('|', ', ')}"
        tip += export_status_hover(status, cats)

        records.append(
            {
                "source": source_key,
                "target": target_key,
                "value": v,
                "color": color,
                "hover": tip,
            }
        )
    return records


def build_sankey_dataset(
    *,
    chart_id: str,
    title: str,
    years: list[int],
    netted_by_year: dict[int, list[dict[str, Any]]],
    gross_by_year: dict[int, list[dict[str, Any]]],
    subtitle: str = "",
    units: EnergyUnit = "twh",
    nodes_by_year: dict[int, list[dict[str, str]]] | None = None,
) -> dict[str, Any]:
    """Build one interactive chart payload keyed by year and netting mode."""
    return {
        "id": chart_id,
        "title": title,
        "subtitle": subtitle,
        "unit": unit_label(units),
        "years": years,
        "netted": {str(y): netted_by_year.get(y, []) for y in years},
        "gross": {str(y): gross_by_year.get(y, []) for y in years},
        "nodes": {str(y): (nodes_by_year or {}).get(y, []) for y in years},
    }


_INTERACTIVE_JS = r"""
function timesPypsaBuildSankey(containerId, links, title, nodeMeta) {
  if (!links || links.length === 0) {
    Plotly.react(containerId, [], {
      title: { text: title + " (no data)" },
      height: 700,
      font: { size: 10 },
    });
    return;
  }
  const nodes = [];
  const nodeColors = [];
  const nodeIndex = {};
  const metaByKey = {};
  (nodeMeta || []).forEach(function (n) { metaByKey[n.key] = n; });

  function nodeIdx(key) {
    if (!(key in nodeIndex)) {
      nodeIndex[key] = nodes.length;
      const meta = metaByKey[key];
      if (meta) {
        nodes.push(meta.label || meta.raw || key);
        nodeColors.push(meta.color || "rgba(120,120,120,0.85)");
      } else {
        const isProc = String(key).startsWith("process::");
        const isCom = String(key).startsWith("commodity::");
        let label = key;
        if (isProc) label = "P · " + key.slice(9);
        else if (isCom) label = "C · " + key.slice(11);
        nodes.push(label);
        nodeColors.push(isProc ? "rgba(70,130,200,0.9)" : (isCom ? "rgba(230,150,50,0.9)" : "rgba(120,120,120,0.85)"));
      }
    }
    return nodeIndex[key];
  }
  const source = [];
  const target = [];
  const value = [];
  const color = [];
  const customdata = [];
  for (const link of links) {
    source.push(nodeIdx(link.source));
    target.push(nodeIdx(link.target));
    value.push(link.value);
    color.push(link.color);
    customdata.push(link.hover);
  }
  const nMax = Math.max(nodes.length, 1);
  const pad = Math.max(4, Math.min(20, Math.floor(300 / nMax)));
  const thickness = Math.max(10, Math.min(30, Math.floor(600 / nMax)));
  Plotly.react(
    containerId,
    [{
      type: "sankey",
      orientation: "h",
      node: {
        pad: pad,
        thickness: thickness,
        line: { color: "rgba(40,40,40,0.65)", width: 0.6 },
        label: nodes,
        color: nodeColors,
      },
      link: {
        source: source,
        target: target,
        value: value,
        color: color,
        customdata: customdata,
        hovertemplate: "%{customdata}<extra></extra>",
      },
    }],
    {
      title: { text: title },
      height: 700,
      font: { size: 10 },
      margin: { l: 20, r: 20, t: 60, b: 40 },
      annotations: [{
        text: "Nodes: P · process (blue family) · C · commodity (amber family)",
        showarrow: false,
        xref: "paper", yref: "paper",
        x: 0, y: -0.06, align: "left",
        font: { size: 11, color: "#444" }
      }],
    }
  );
}

function timesPypsaInitSankeyChart(chart) {
  const root = document.getElementById("chart-" + chart.id);
  if (!root) return;
  const plotDiv = root.querySelector(".sankey-plot");
  const yearSlider = root.querySelector(".year-slider");
  const yearLabel = root.querySelector(".year-label");
  const nettingToggle = root.querySelector(".netting-toggle");
  const years = chart.years.map(String);
  const unit = chart.unit || "TWh";
  let yearIdx = years.length - 1;

  function currentLinks() {
    const year = years[yearIdx];
    const variant = nettingToggle.checked ? chart.netted : chart.gross;
    return variant[year] || [];
  }

  function currentNodes() {
    const year = years[yearIdx];
    return (chart.nodes && chart.nodes[year]) || [];
  }

  function render() {
    const year = years[yearIdx];
    yearLabel.textContent = year;
    yearSlider.value = String(yearIdx);
    const mode = nettingToggle.checked ? "netted" : "gross";
    const title = chart.title + " — " + year + " (" + unit + ", " + mode + " flows)";
    timesPypsaBuildSankey(plotDiv.id, currentLinks(), title, currentNodes());
  }

  yearSlider.min = "0";
  yearSlider.max = String(Math.max(years.length - 1, 0));
  yearSlider.step = "1";
  yearSlider.addEventListener("input", function () {
    yearIdx = parseInt(yearSlider.value, 10);
    render();
  });
  nettingToggle.addEventListener("change", render);
  render();
}

function timesPypsaInitSankeys(charts) {
  for (const chart of charts) {
    timesPypsaInitSankeyChart(chart);
  }
}
"""


def render_interactive_sankey_section(chart: dict[str, Any]) -> str:
    """Return HTML block for one interactive Sankey (controls + plot div)."""
    chart_id = chart["id"]
    subtitle = chart.get("subtitle", "")
    subtitle_html = f"<p class='meta'>{subtitle}</p>" if subtitle else ""
    return f"""
<section class="sankey-section" id="chart-{chart_id}">
  <div class="sankey-controls">
    <label class="control">
      <span class="control-label">Year</span>
      <input type="range" class="year-slider" aria-label="Select year">
      <output class="year-label"></output>
    </label>
    <label class="control netting-control">
      <input type="checkbox" class="netting-toggle" checked>
      Net bidirectional flows
    </label>
  </div>
  {subtitle_html}
  <div id="plot-{chart_id}" class="sankey-plot"></div>
</section>
"""


def assemble_interactive_report_html(
    *,
    page_title: str,
    header_html: str,
    charts: list[dict[str, Any]],
    footer_html: str,
) -> str:
    """Assemble a full HTML page with embedded Sankey data and controls."""
    charts_json = json.dumps(charts, ensure_ascii=False)
    sections = "\n".join(render_interactive_sankey_section(c) for c in charts)
    return f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="utf-8">
  <title>{page_title}</title>
  <script src="https://cdn.plot.ly/plotly-2.35.2.min.js"></script>
  <style>
    body {{ font-family: system-ui, sans-serif; margin: 24px; max-width: 1400px; }}
    h1, h2, h3 {{ color: #222; }}
    .meta {{ color: #555; }}
    table {{ border-collapse: collapse; font-size: 13px; }}
    th, td {{ border: 1px solid #ccc; padding: 4px 8px; }}
    th {{ background: #f0f0f0; }}
    .ok {{ color: green; }}
    .bad {{ color: #b00; }}
    .legend span {{
      display: inline-block; padding: 2px 8px; margin: 2px; border-radius: 3px;
    }}
    .verdict {{
      padding: 12px 16px; background: #f7f7f7;
      border-left: 4px solid #1f77b4; margin: 16px 0;
    }}
    .sankey-section {{ margin: 32px 0 48px; }}
    .sankey-controls {{
      display: flex; flex-wrap: wrap; gap: 24px; align-items: center;
      padding: 12px 16px; background: #fafafa; border: 1px solid #e0e0e0;
      border-radius: 6px; margin-bottom: 12px;
    }}
    .control {{ display: flex; align-items: center; gap: 10px; }}
    .control-label {{ font-weight: 600; min-width: 36px; }}
    .year-slider {{ width: min(420px, 70vw); vertical-align: middle; }}
    .year-label {{
      font-weight: 600; min-width: 48px; display: inline-block;
    }}
    .netting-control {{ cursor: pointer; }}
    .sankey-plot {{ width: 100%; min-height: 700px; }}
  </style>
</head>
<body>
{header_html}
{sections}
{footer_html}
<script>
const TIMES_PYSA_SANKEYS = {charts_json};
{_INTERACTIVE_JS}
document.addEventListener("DOMContentLoaded", function () {{
  timesPypsaInitSankeys(TIMES_PYSA_SANKEYS);
}});
</script>
</body>
</html>
"""
