"""Interactive multi-year Sankey HTML (standalone, not SEPIA)."""

from __future__ import annotations

import colorsys
import hashlib
import json
from typing import Any

import pandas as pd

from times_pypsa.aggregation import (
    PYPSA_SECTOR_COLORS,
    PYPSA_SECTOR_ORDER,
    sector_color,
)
from times_pypsa.units import EnergyUnit, pj_to_display, unit_label

EXPORTED_COLOR = "rgba(31, 119, 180, 0.85)"
CONTEXT_COLOR = "rgba(160, 160, 160, 0.45)"
MIXED_COLOR = "rgba(255, 152, 152, 0.75)"
# Both FOut and FIn endpoints exported on a collapsed link (soft-link double-count risk)
DOUBLE_COUNT_COLOR = "rgba(142, 68, 173, 0.85)"
# Grey ribbon landing on a node whose *other* inflows of the same commodity ARE
# soft-linked. Grey is the diagram's default state — it is correct for the whole
# upstream supply chain — so an omission at a demand node is visually identical to
# ordinary context. This colour separates the two. See `flag_leak_suspects`.
LEAK_COLOR = "rgba(230, 85, 13, 0.85)"

# Process / commodity nodes → green (distinct from blue exported links);
# imbalance residual → magenta.
_PROCESS_HSV = (135.0, 0.55, 0.68)
_COMMODITY_HSV = (145.0, 0.50, 0.72)
_IMBALANCE_HSV = (310.0, 0.65, 0.78)

_NODE_KINDS = frozenset({"process", "commodity", "imbalance"})


def _normalize_kind(kind: str) -> str:
    k = str(kind or "").strip().lower()
    if k in _NODE_KINDS:
        return k
    if k.startswith("p"):
        return "process"
    if k.startswith("c"):
        return "commodity"
    if k.startswith("i") or k.startswith("u"):
        return "imbalance"
    return "process"


def export_status_color(export_status: str) -> str:
    if export_status == "exported":
        return EXPORTED_COLOR
    if export_status == "double_count":
        return DOUBLE_COUNT_COLOR
    if export_status == "mixed":
        return MIXED_COLOR
    return CONTEXT_COLOR


def link_color(export_status: str, export_sector: str = "") -> str:
    """Colour a Sankey link by PyPSA sector when exported, else by status.

    Exported links are coloured by their PyPSA end-use sector (Industry,
    Transport, Residential, Services, Agriculture). ``double_count`` (both FOut
    and FIn soft-linked — a genuine overlap that should not occur) keeps its
    purple warning colour; context links are grey.
    """
    sec = str(export_sector or "").strip()
    if sec:
        return sector_color(sec)
    return export_status_color(export_status)


def link_export_status(row: pd.Series) -> str:
    status = str(row.get("export_status", "") or "").strip()
    if status in {"exported", "context", "mixed", "double_count"}:
        return status
    if bool(row.get("exported", False)):
        return "exported"
    return "context"


def flag_leak_suspects(
    links: pd.DataFrame,
    service_carriers: frozenset[str] | None = None,
) -> pd.DataFrame:
    """
    Mark grey ribbons whose *siblings* into the same node, on the same commodity,
    are soft-linked.

    Grey ("not exported") is the Sankey's default and correct state for the entire
    upstream supply chain, so a soft-link omission at a demand node renders exactly
    like ordinary context and the reader has no cue to look. In the 2026 Walloon
    heat leak, 14 grey ribbons landed on ``Buildings: built area`` alongside 25
    correctly coloured ones; four of them carried no label at all (the process was
    in neither mapping CSV, so there was no description to draw), and each was
    individually 0.2–2 % of the diagram. Only in aggregate were they material, and
    the eye does not integrate 14 thin ribbons.

    The signal used here needs no extra metadata: *if a node receives commodity C
    from several processes and some of those ribbons are exported while others are
    not, the grey ones are suspects.*

    Restricted to ``service_carriers`` — the carriers the extraction rules claim to
    transfer as a *service output*, i.e. where full coverage is the invariant.
    Without that scope the rule fires on every partially-exported carrier by
    design: electricity into ``Industry`` is legitimately split between anchored
    fuel-tech ribbons (coloured) and power-plant ribbons (grey), which on the
    reference scenario would paint 541 ribbons / 182 PJ orange and re-create the
    cry-wolf problem this is meant to solve.

    Adds a boolean ``leak_suspect`` column. Statuses are left untouched, so
    netting, reconciliation, and the export-sector logic are unaffected — this is
    presentation only.
    """
    out = links.copy() if links is not None else pd.DataFrame()
    if out.empty or not {"target", "value"}.issubset(out.columns):
        if not out.empty:
            out["leak_suspect"] = False
        return out
    if service_carriers is None:
        from times_pypsa.balances import SERVICE_OUTPUT_CARRIERS

        service_carriers = SERVICE_OUTPUT_CARRIERS

    status = out.apply(link_export_status, axis=1)
    exported = status.isin(("exported", "double_count", "mixed"))
    commodity = (
        out["commodity"].astype(str)
        if "commodity" in out.columns
        else pd.Series("", index=out.index)
    )
    # Post-collapse a ribbon may carry several commodities ("Heat|Electricity");
    # in scope if any part is a service carrier.
    in_scope = commodity.map(
        lambda c: any(p.strip() in service_carriers for p in str(c).split("|"))
    )
    key_ser = pd.Series(list(zip(out["target"].astype(str), commodity)), index=out.index)
    coloured_keys = set(key_ser[exported & in_scope])
    out["leak_suspect"] = (~exported) & in_scope & key_ser.isin(coloured_keys)
    return out


def _format_category_phrase(cats: str) -> str:
    """Turn ``a|b`` / ``a, b`` into a short phrase for hover text."""
    parts: list[str] = []
    seen: set[str] = set()
    raw = str(cats or "").replace("|", ",")
    for part in raw.split(","):
        name = part.strip()
        if name and name not in seen:
            seen.add(name)
            parts.append(name)
    if not parts:
        return ""
    if len(parts) == 1:
        return f"category '{parts[0]}'"
    joined = ", ".join(f"'{p}'" for p in parts)
    return f"categories {joined}"


def export_status_hover(
    export_status: str,
    cats: str = "",
    *,
    export_detail: str = "",
    export_sector: str = "",
) -> str:
    """Hover fragment for link export colouring (HTML ``<br>…``)."""
    detail = str(export_detail or "").strip()
    sector = str(export_sector or "").strip()
    cat_bit = _format_category_phrase(cats)
    if export_status == "exported":
        head = f"Exported to pypsa-wal · {sector}" if sector else "Exported to pypsa-wal"
        bits = [head]
        if cat_bit:
            bits.append(cat_bit)
        if detail:
            bits.append(f"via {detail}")
        return "<br>" + " ".join(bits)
    if export_status == "double_count":
        bits = ["Double-count risk: both FOut and FIn exported"]
        if cat_bit:
            bits.append(f"({cat_bit})")
        if detail:
            bits.append(f"via {detail}")
        return "<br>" + " ".join(bits)
    if export_status == "mixed":
        tip = "<br>Mixed link (exported and non-exported on the same endpoint)"
        if cat_bit:
            tip += f"<br>Export {cat_bit}"
        if detail:
            tip += f"<br>Export side: {detail}"
        return tip
    if export_status == "context":
        return "<br>Not exported to pypsa-wal"
    return ""


def node_key(kind: str, label: str) -> str:
    """Unique id so typed nodes that share a display name stay distinct."""
    return f"{_normalize_kind(kind)}::{label}"


def node_display_label(kind: str, label: str) -> str:
    """Display label for a typed Sankey node.

    Process/commodity nodes use the raw name (commodities are flows after collapse,
    so a P/C prefix is unnecessary). Imbalance / expected-demand residuals keep
    the ``U ·`` marker (magenta non-energy endpoints).
    """
    kind_n = _normalize_kind(kind)
    if kind_n == "imbalance":
        return f"U · {label}"
    return str(label)


def node_kind_color(kind: str, label: str) -> str:
    """Vary colour within the process / commodity / imbalance hue family."""
    kind_n = _normalize_kind(kind)
    base_h, base_s, base_v = {
        "process": _PROCESS_HSV,
        "commodity": _COMMODITY_HSV,
        "imbalance": _IMBALANCE_HSV,
    }[kind_n]
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
                    "hover": name,
                }
            )
        return ordered

    for _, row in links.iterrows():
        for kind_col, name_col in (("source_kind", "source"), ("target_kind", "target")):
            label = str(row[name_col])
            kind = _normalize_kind(str(row.get(kind_col) or ""))
            key = node_key(kind, label)
            if key in seen:
                continue
            seen.add(key)
            tip = node_display_label(kind, label)
            if kind == "imbalance":
                tip = str(row.get("imbalance_tooltip") or "").strip() or tip
            ordered.append(
                {
                    "key": key,
                    "label": node_display_label(kind, label),
                    "kind": kind,
                    "raw": label,
                    "color": node_kind_color(kind, label),
                    "hover": tip,
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
        sector = str(row.get("export_sector", "") or "").strip()
        leak = bool(row.get("leak_suspect", False))
        # A leak suspect keeps status `context` everywhere else in the pipeline;
        # only its colour changes, so it stops hiding among legitimate grey.
        color = LEAK_COLOR if leak else link_color(status, sector)

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
        # Categories are folded into the export line when the link is exported.
        if cats and status == "context":
            tip += f"<br>Categories: {cats.replace('|', ', ')}"
        commodity = str(row.get("commodity", "") or "")
        if commodity:
            tip += f"<br>Commodity flow: {commodity.replace('|', ', ')}"
        if leak:
            tip += (
                "<br><b>NOT exported to pypsa-wal — but sibling flows into this "
                "node on the same commodity ARE.</b><br>Likely a missing "
                "<code>Aggregation Level 2</code> label or extraction rule. See "
                "the <i>Extraction integrity</i> section."
            )
        else:
            tip += export_status_hover(
                status,
                cats,
                export_detail=str(row.get("export_detail", "") or ""),
                export_sector=sector,
            )
        imb_tip = str(row.get("imbalance_tooltip", "") or "").strip()
        if imb_tip and has_kinds and (
            str(row.get("source_kind", "")) == "imbalance"
            or str(row.get("target_kind", "")) == "imbalance"
        ):
            tip += f"<br>{imb_tip}"

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


def _fallback_node_entry(key: str) -> dict[str, str]:
    """Minimal node metadata when a link endpoint was not in collect_typed_nodes."""
    kind_raw, _, raw = str(key).partition("::")
    kind = _normalize_kind(kind_raw if raw else "process")
    label_raw = raw or kind_raw or key
    tip = node_display_label(kind, label_raw)
    return {
        "key": key,
        "label": tip,
        "kind": kind,
        "raw": label_raw,
        "color": node_kind_color(kind, label_raw),
        "hover": tip,
    }


def ensure_sankey_node_coverage(
    nodes: list[dict[str, str]],
    links: list[dict[str, Any]],
) -> list[dict[str, str]]:
    """Ensure every link source/target key has node metadata (netted ∪ gross)."""
    known = {n["key"] for n in nodes}
    out = list(nodes)
    for link in links:
        for side in ("source", "target"):
            key = str(link.get(side) or "")
            if not key or key in known:
                continue
            known.add(key)
            out.append(_fallback_node_entry(key))
    return out


def validate_sankey_chart(chart: dict[str, Any]) -> list[str]:
    """Return human-readable issues in an embedded Sankey chart payload."""
    issues: list[str] = []
    chart_id = str(chart.get("id", ""))
    for y in chart.get("years", []):
        ys = str(y)
        node_keys = {n["key"] for n in chart.get("nodes", {}).get(ys, [])}
        for mode in ("netted", "gross"):
            variant = chart.get(mode) or {}
            links = variant.get(ys, [])
            if links is None:
                issues.append(f"{chart_id}/{ys}: {mode} links is null")
                continue
            for i, link in enumerate(links):
                for side in ("source", "target"):
                    key = link.get(side)
                    if not key:
                        issues.append(f"{chart_id}/{ys}/{mode}[{i}]: missing {side}")
                    elif key not in node_keys:
                        issues.append(
                            f"{chart_id}/{ys}/{mode}[{i}]: {side} {key!r} not in nodes"
                        )
                value = link.get("value")
                if value is None or not isinstance(value, (int, float)):
                    issues.append(f"{chart_id}/{ys}/{mode}[{i}]: invalid value")
                elif value <= 0:
                    issues.append(f"{chart_id}/{ys}/{mode}[{i}]: non-positive value")
    return issues


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
    netted: dict[str, list[dict[str, Any]]] = {}
    gross: dict[str, list[dict[str, Any]]] = {}
    nodes: dict[str, list[dict[str, str]]] = {}
    for y in years:
        netted_links = netted_by_year.get(y, [])
        gross_links = gross_by_year.get(y, [])
        netted[str(y)] = netted_links
        gross[str(y)] = gross_links
        nodes[str(y)] = ensure_sankey_node_coverage(
            (nodes_by_year or {}).get(y, []),
            netted_links + gross_links,
        )
    return {
        "id": chart_id,
        "title": title,
        "subtitle": subtitle,
        "unit": unit_label(units),
        "years": years,
        "netted": netted,
        "gross": gross,
        "nodes": nodes,
    }


_INTERACTIVE_JS = r"""
function timesPypsaSankeyPlot(containerId, data, layout) {
  // Plotly.react can crash when Sankey node/link counts change (netted ↔ gross).
  if (typeof Plotly.purge === "function") {
    Plotly.purge(containerId);
  }
  return Plotly.newPlot(containerId, data, layout, { responsive: true });
}

function timesPypsaBuildSankey(containerId, links, title, nodeMeta) {
  const layout = {
    title: { text: title },
    height: 700,
    font: { size: 10 },
    margin: { l: 20, r: 20, t: 60, b: 40 },
    annotations: [{
      text: "Nodes: process (green) · U · demand/imbalance residual (magenta). Commodities are flows. Exported links coloured by PyPSA sector: Industry (blue) · Transport (orange) · Residential (red) · Services (teal) · Agriculture (brown). Grey = not exported; purple = double-count (FOut+FIn both exported, should not occur).",
      showarrow: false,
      xref: "paper", yref: "paper",
      x: 0, y: -0.08, align: "left",
      font: { size: 11, color: "#444" }
    }],
  };
  if (!links || links.length === 0) {
    timesPypsaSankeyPlot(containerId, [], Object.assign({}, layout, {
      title: { text: title + " (no data)" },
    }));
    return;
  }
  const nodes = [];
  const nodeColors = [];
  const nodeHover = [];
  const nodeIndex = {};
  const metaByKey = {};
  (nodeMeta || []).forEach(function (n) {
    if (n && n.key != null) metaByKey[n.key] = n;
  });

  function addNode(key, meta) {
    if (key == null || key in nodeIndex) return;
    nodeIndex[key] = nodes.length;
    if (meta) {
      const label = meta.label || meta.raw || key;
      nodes.push(label);
      nodeColors.push(meta.color || "rgba(120,120,120,0.85)");
      nodeHover.push(meta.hover || label);
      return;
    }
    const keyStr = String(key);
    const isProc = keyStr.startsWith("process::");
    const isCom = keyStr.startsWith("commodity::");
    const isImb = keyStr.startsWith("imbalance::");
    let label = keyStr;
    if (isProc) label = keyStr.slice(9);
    else if (isCom) label = keyStr.slice(11);
    else if (isImb) label = "U · " + keyStr.slice(11);
    nodes.push(label);
    nodeColors.push(
      isProc ? "rgba(90,194,111,0.92)"
        : (isCom ? "rgba(90,194,111,0.92)"
          : (isImb ? "rgba(180,60,160,0.9)" : "rgba(120,120,120,0.85)"))
    );
    nodeHover.push(label);
  }

  (nodeMeta || []).forEach(function (n) {
    if (n && n.key != null) addNode(n.key, n);
  });

  const source = [];
  const target = [];
  const value = [];
  const color = [];
  const customdata = [];
  for (const link of links) {
    if (!link) continue;
    const srcKey = link.source;
    const tgtKey = link.target;
    const v = Number(link.value);
    if (srcKey == null || tgtKey == null || !isFinite(v) || v <= 0) continue;
    addNode(srcKey, metaByKey[srcKey]);
    addNode(tgtKey, metaByKey[tgtKey]);
    const srcIdx = nodeIndex[srcKey];
    const tgtIdx = nodeIndex[tgtKey];
    if (srcIdx == null || tgtIdx == null) continue;
    source.push(srcIdx);
    target.push(tgtIdx);
    value.push(v);
    color.push(link.color);
    customdata.push(link.hover);
  }
  if (source.length === 0) {
    timesPypsaSankeyPlot(containerId, [], Object.assign({}, layout, {
      title: { text: title + " (no data)" },
    }));
    return;
  }
  const nMax = Math.max(nodes.length, 1);
  const pad = Math.max(4, Math.min(20, Math.floor(300 / nMax)));
  const thickness = Math.max(10, Math.min(30, Math.floor(600 / nMax)));
  timesPypsaSankeyPlot(
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
        customdata: nodeHover,
        hovertemplate: "%{customdata}<extra></extra>",
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
    layout
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
    const variant = nettingToggle.checked ? (chart.netted || {}) : (chart.gross || {});
    return variant[year] || [];
  }

  function currentNodes() {
    const year = years[yearIdx];
    return (chart.nodes && chart.nodes[year]) || [];
  }

  function render() {
    if (!plotDiv || !yearLabel || !yearSlider || !nettingToggle) return;
    const year = years[yearIdx];
    yearLabel.textContent = year;
    yearSlider.value = String(yearIdx);
    const mode = nettingToggle.checked ? "netted" : "gross";
    const title = chart.title + " — " + year + " (" + unit + ", " + mode + " flows)";
    try {
      timesPypsaBuildSankey(plotDiv.id, currentLinks(), title, currentNodes());
    } catch (err) {
      console.error("Sankey render failed:", chart.id, mode, year, err);
      timesPypsaSankeyPlot(plotDiv.id, [], {
        title: { text: title + " (render error)" },
        height: 700,
        font: { size: 10 },
      });
    }
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
    .alarm {{
      padding: 12px 16px; background: #fdf0ef;
      border-left: 4px solid #b00; margin: 16px 0;
    }}
    .alarm h2, .alarm h3 {{ color: #b00; margin-top: 0; }}
    .warn {{
      padding: 12px 16px; background: #fff8e8;
      border-left: 4px solid #d68910; margin: 16px 0;
    }}
    .warn h2, .warn h3 {{ color: #a15c00; margin-top: 0; }}
    .clear {{
      padding: 12px 16px; background: #f2f8f2;
      border-left: 4px solid #2a7; margin: 16px 0;
    }}
    .clear h2, .clear h3 {{ color: #176; margin-top: 0; }}
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
