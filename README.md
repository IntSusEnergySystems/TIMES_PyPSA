### TIMES_PyPSA

Soft-linking between the TIMES-WAL and PyPSA-WAL models. This repository provides the **`times_pypsa`** Python package to parse TIMES `.vd` output files, extract PyPSA demand categories, generate interactive Sankey energy-flow diagrams, and run multi-view extraction quality assurance.

Related: [SOFTLINKING_ANALYSIS.md](SOFTLINKING_ANALYSIS.md) for architecture, coupling workflow, verification, and open questions. [aggregation.md](aggregation.md) for Sankey aggregation levels and export colouring.
Important note: when testing, and troubleshooting, always use the 'custom' aggregation level.

### Soft-linking is universal (no ad-hoc flow drops)

Every soft-link category in `extraction_rules.csv` is treated the same way in QA and Sankeys:

- **Do not** drop, hide, or special-case individual soft-linked processes or categories to “clean up” a diagram (e.g. building retrofits).
- If a TIMES structure looks wrong or obscure on the Sankey, **understand it**, fix **labels/mappings**, or leave an explicit note — never remove matched export flows from the pipeline.
- The only energy-Sankey scope filter is universal and carrier-based (`filter_energy_carrier_flows`): keep flows with a mapped `pypsa_carrier`, drop emission/pollutant codes. That filter applies to all categories equally; it is not a soft-link exception list.

### Install

From the repository root:

```bash
python -m pip install --upgrade pip
pip install -e .
```

Dependencies: `pandas`, `plotly` (Python 3.9+).

Bundled mapping files live in `data/` (`mapping_commodities.csv`, `mapping_processes.csv`, `extraction_rules.csv`).

For tests, install dev dependencies: `pip install -e ".[dev]"`.

### CLI usage

Export demands and Sankey for multiple horizons:

```bash
times-pypsa export \
  --vd data/scen_corrige_251129_0112.vd \
  --out output/ \
  --horizons 2021-2050 \
  --emit all
```

Export demands only:

```bash
times-pypsa export \
  --vd data/scen_corrige_251129_0112.vd \
  --out output/ \
  --horizons 2030,2040,2050 \
  --emit demands
```

Generate a standalone Sankey for one year:

```bash
times-pypsa sankey \
  --vd data/scen_corrige_251129_0112.vd \
  --year 2030 \
  --out-dir output/
```

Multi-view extraction QA (full HTML report + balance / coverage CSVs):

```bash
times-pypsa qa \
  --vd data/scen_corrige_251129_0112.vd \
  --vdt data/scen_corrige_251129_0112.vdt \
  --out-dir output/qa/ \
  --agg-level "Aggregation Level 2"
```

Coarse overview report (≤20 Sankey nodes; see [aggregation.md](aggregation.md)):

```bash
times-pypsa qa \
  --vd data/scen_corrige_251129_0112.vd \
  --vdt data/scen_corrige_251129_0112.vdt \
  --out-dir output/qa_overview/ \
  --agg-level sankey_overview
```

Working / editable aggregation (`custom` starts as a copy of Aggregation Level 2):

```bash
times-pypsa qa \
  --vd data/scen_corrige_251129_0112.vd \
  --vdt data/scen_corrige_251129_0112.vdt \
  --out-dir output/qa_custom/ \
  --agg-level custom
```

Omit `--year` to include all model years in the interactive HTML report (year slider + flow-netting toggle on each Sankey). Optional: `--year 2050`, `--year 2030,2040,2050`, or `--year 2025-2050`. Energy values default to **TWh** (`--units pj` for petajoules). `--threshold-export` is interpreted in the selected unit (default **0**, keep all links). `--agg-level` selects the shared mapping CSV column used for process × commodity node labels (default: `Aggregation Level 2`). See [Extraction QA](#extraction-qa) and [aggregation.md](aggregation.md).

Use `--mappings-dir` to override the default mappings (defaults to the repository `data/` directory).

### Soft-linking bundle (PyPSA-WAL)

Export a coupling directory for pypsa-wal (TIMES `.vd`, mappings, pre-exported demands):

```bash
times-pypsa export-coupling \
  --coupling-dir /path/to/coupling_run \
  --vd data/scen_corrige_251129_0112.vd \
  --horizons 2025,2030,2040,2050
```

Or use the helper script (export only by default; add `--snakemake` to build demands in pypsa-wal):

```bash
./scripts/run_coupled.sh /path/to/coupling_run --snakemake
```

In pypsa-wal, set `coupling_dir` in `config/config.walloon.yaml` (or via `--config coupling_dir=...`) so `build_wallon_demands` copies pre-exported CSVs instead of re-parsing the `.vd`.

### Python API

For Snakemake integration in pypsa-wal:

```python
from times_pypsa import export_horizon, export_all_horizons, generate_sankey

export_horizon(
    vd_file="path/to/scenario.vd",
    mappings_dir="data",
    horizon=2030,
    wallon_demands_path="resources/walloon/demands_2030.csv",
    heating_capacities_path="resources/walloon/heating_capacities_2030.csv",
    sankey_dir="results/sankey",
    emit_sankey=True,
)
```

For enriched annual flows and topology (QA / analysis):

```python
from times_pypsa import load_times_annual_flows, load_topology

model = load_times_annual_flows(
    "scen.vd",
    mappings_dir="data",
    vdt_file="scen.vdt",
)
```

### Legacy script

The original monolithic script remains as a thin wrapper:

```bash
python scripts/bau_sankey_diagram.py
```

This exports `pypsa_demands_{year}.csv` for 2021–2050 and a Sankey for 2030 into `output/`.

> **Note:** `scripts/extract_pypsa_demands.py` is archived. It used an older mapping-based approach; use `times-pypsa export` or the Python API instead.

### Data

Files under `data/`:

- `scen_*.vd`: TIMES scenario output files
- `scen_*.vdt`: TIMES topology (process–commodity wiring, no quantities)
- `AllProcesses.csv`, `AllCommodities.csv`: full VEDA dictionaries (semicolon-separated) with **Name** + **Description** for nearly all model codes — used to name Sankey fallbacks and to debug unmapped flows
- `mapping_commodities.csv`, `mapping_processes.csv`, `extraction_rules.csv`: TIMES → PyPSA mappings (in `data/`)

**Reference scenario for QA:** `data/scen_corrige_251129_0112.{vd,vdt}`

### Example outputs

Generated files (synced to the web server by the rsync script):

- [bau_sankey_2021_pj_clustered.html](http://labothap.squoilin.eu/times_pypsa/bau_sankey_2021_pj_clustered.html)
- [bau_sankey_2050_pj.html](http://labothap.squoilin.eu/times_pypsa/bau_sankey_2050_pj.html)
- [annual_values_2021.csv](http://labothap.squoilin.eu/times_pypsa/annual_values_2021.csv)
- [annual_values_clustered.csv](http://labothap.squoilin.eu/times_pypsa/annual_values_clustered.csv)

---

### Extraction QA

Documentation for the TIMES → PyPSA soft-link extraction quality-assurance toolkit: data model, aggregation levels, multi-view Sankey report, and tests.

#### Design summary

- **Extraction** filters on `mapping_processes.csv` **Aggregation Level 2** (column `process_agg`), `mapping_commodities.csv` **PyPSA Energy Carrier**, and the categories in `extraction_rules.csv`. Changing a Sankey/QA aggregation level never changes the exported demands.
- **Aggregation levels** are shared CSV column names selected with `--agg-level` / `aggregate_flows(level=...)`: `Sector`, `Aggregation Level 1`, `Aggregation Level 2` (default; alias `mapping`), `custom` (readable working level: export-touching L2 kept, context collapsed), `sankey_overview` (≤20 nodes). Details: [aggregation.md](aggregation.md).
- **Sankey** collapses commodity hubs to process→process flows (commodities become link labels). Process nodes are green; expected final-demand / non-PJ residuals become magenta `U ·` sink nodes; unexplained imbalances keep `Unbalanced …` labels and warn/error. Exported links are coloured by **PyPSA sector** and anchored to the demand inflow (see [aggregation.md](aggregation.md#export-strategy-per-sector-colours--demand-inflow-anchoring)).
- **Adequacy**: parent–child heat identities hold and topology is clean for the reference scenario; the residential-cooking gap and the biofuel-blending double-count are fixed. The remaining DMD coverage gap is consumption-side (n+1) of already-exported production — see [Open points](#open-points).

#### TIMES data model

##### `.vd` — results (GDX2VEDA)

Sparse table with dimensions:

`Attribute, Commodity, Process, Period, Region, Vintage, TimeSlice, UserConstraint, PV`

| Attribute | Role for soft-linking |
|-----------|------------------------|
| `VAR_FIn` | Commodity **input** to a process (fuel/feedstock), PJ |
| `VAR_FOut` | Commodity **output** from a process, PJ |
| `VAR_Comnet` | Net commodity production (region balance oracle) |
| `VAR_Cap` / `VAR_Ncap` | Capacities (heating stock export) |
| `VAR_Act` | Process activity (no commodity dimension) |

Energy Sankeys and demand extraction use **only** `VAR_FIn` / `VAR_FOut`, annualized by summing timeslices (and effectively summing vintages via the groupby that drops vintage).

**Link convention (Sankey):**

- `VAR_FIn`: source = commodity → target = process
- `VAR_FOut`: source = process → target = commodity

##### `.vdt` — topology

Static wiring: `Region, Process, Commodity, Direction` with `IN` or `OUT`. Does not contain quantities. Used to flag result flows that are absent from the declared topology (`times_pypsa.topology.load_topology`).

##### Package representation

| Module | Type |
|--------|------|
| `times_pypsa.topology.Topology` | `.vdt` links |
| `times_pypsa.model.TimesAnnualFlows` | Enriched annual flows + `VAR_Comnet` + optional topology mismatches |
| `times_pypsa.aggregation` | L0 / L1 / L2 collapse |
| `times_pypsa.qa` | Multi-view HTML report |
| `times_pypsa.balances` | Balance / loop / double-count helpers |

Enriched flow columns include: `sector`, `agg_level_1`, `agg_level_2`, `process_agg`, `pypsa_carrier`, `commodity_sector`, plus `proc_agg__{level}` / `com_agg__{level}` for every shared aggregation column.

##### `process_agg` naming (important)

Extraction rules filter on **Aggregation Level 2** labels from `mapping_processes.csv`. Historically those labels were stored in a column named `agg_level_1`, which was confusing because Aggregation Level 1 also exists.

**Canonical column:** `process_agg` (= Aggregation Level 2).  
`agg_level_1` is kept as a **legacy alias** of `process_agg` inside the extractor so existing rule CSVs keep working. True Aggregation Level 1 is available on `TimesAnnualFlows.flows["agg_level_1"]` only when loaded via `load_times_annual_flows` (model path); the demand extractor still mirrors Level 2 into both `process_agg` and `agg_level_1`.

#### Aggregation levels (Sankey / QA only)

Extraction itself is unchanged (filters on Aggregation Level 2 labels). Sankey / QA aggregation is selected by a **single shared CSV column name** (process mapping × commodity mapping).

| Level (`--agg-level`) | Typical use |
|-----------------------|-------------|
| **`Aggregation Level 2`** (default; alias `mapping`) | Export neighbourhood / detailed QA |
| **`custom`** | Working level: export-touching L2 kept, context collapsed (~45 nodes) |
| `Aggregation Level 1` / `Sector` / `L2` | Mid / coarse / fine drill-down |
| **`sankey_overview`** | Whole-system Sankey (≤20 nodes) |

**No opaque placeholders.** Empty mapping cells fall back to TIMES Description, then code — never `Unknown`.

Full documentation (how to add levels, commodity-hub collapse, node/link colours, `custom` design, and remaining limits) lives in **[aggregation.md](aggregation.md)**.

##### Quick colour legend

| Element | Colour |
|---------|--------|
| Process nodes | green |
| Imbalance residual nodes | magenta (`U ·`; expected sinks use process name) |
| Exported links | **by PyPSA sector**: Industry (blue) · Transport (orange) · Residential (red) · Services (teal) · Agriculture (brown) |
| Context links | grey |
| Double-count links | purple (both FOut+FIn exported; should not occur) |

Exported links are coloured by their PyPSA end-use sector, and soft-linked fuel
inputs are **anchored to the demand inflow** (e.g. industry fuel exports sit on
`Fuel Tech (IND) → Industry`, not the upstream import link). The former single
blue / light-red *mixed* scheme is retired (mixed verified never to occur). See
[aggregation.md](aggregation.md#export-strategy-per-sector-colours--demand-inflow-anchoring).

```bash
times-pypsa qa \
  --vd data/scen_corrige_251129_0112.vd \
  --vdt data/scen_corrige_251129_0112.vdt \
  --out-dir output/qa_custom/ \
  --agg-level custom
```

##### Mapping Sankey nodes back to TIMES codes

When debugging a Sankey node, use `qa_sankey_label_map_{year}.csv` next to the HTML report (TIMES codes under each aggregated label). See [aggregation.md § Mapping Sankey nodes](aggregation.md#mapping-sankey-nodes-back-to-times-codes).

##### Export neighbourhood (n−1 / n / n+1)

Core = codes on extraction-matched flows; keep every energy flow sharing a core process or commodity (upstream n−1, matched n, downstream n+1). Details: [aggregation.md](aggregation.md#export-neighbourhood-n1--n--n1).

#### QA report output

The `times-pypsa qa` command (see [CLI usage](#cli-usage)) writes `qa_report.html` (interactive Sankeys with year timeline and flow-netting toggle) plus per-year CSVs `qa_*_{year}.csv`.

##### Views

| View | Content |
|------|---------|
| **A** | Whole TIMES energy flows (selected `--agg-level`; year slider + netting toggle) |
| **B** | Export neighbourhood Sankey (same agg level; blue=exported, grey=n−1/n+1) |
| **C** | Per-category neighbourhoods (same rule, matched + n−1 + n+1) |
| **D** | Tables: coverage, empty rules, parent–child, double-count, Comnet, loops, DMD gaps |

##### Companion CSVs

- `qa_flows_{year}.csv` — tagged annual energy flows at TIMES process/commodity resolution
- `qa_sankey_label_map_{year}.csv` — **Sankey label → TIMES code crosswalk** (see [aggregation.md](aggregation.md#mapping-sankey-nodes-back-to-times-codes))
- `qa_export_neighborhood_{year}.csv` — matched + n−1 + n+1 rows before Sankey aggregation
- `qa_export_coverage_{year}.csv` — exported energy per category (in selected units)
- `qa_node_balance_{year}.csv` — ΣFOut−ΣFIn vs `VAR_Comnet`
- `qa_commodity_residuals_{year}.csv` — post-netting commodity residuals
- `qa_loops_{year}.csv` — SCCs with >1 node after netting
- `qa_double_count_{year}.csv` — disallowed category overlaps
- `qa_parent_child_{year}.csv` — heat / agriculture identities
- `qa_empty_rules_{year}.csv` — zero categories vs known-zero allowlist
- `qa_coverage_gap_{year}.csv` — DMD demand-sector `VAR_FIn` not matched by any rule
- `qa_coverage_gap_all_{year}.csv` — same without DMD filter
- `qa_topology_mismatches_{year}.csv` — if `.vdt` provided and mismatches remain

**Where to edit extraction and QA code** (see also [SOFTLINKING_ANALYSIS.md §4](SOFTLINKING_ANALYSIS.md#4-extraction-mechanics-edit-here-when-changing-results)):

| What | File |
|------|------|
| Category definitions / filters | `data/extraction_rules.csv` |
| Process aggregation labels | `mapping_processes.csv` |
| Commodity → PyPSA carrier | `mapping_commodities.csv` |
| Parser / netting / road-rail | `times_pypsa/pipeline.py` |
| QA report / balances / topology | `times_pypsa/{qa,balances,model,topology,aggregation}.py` |

#### Tests

```bash
cd TIMES_PyPSA
pip install -e ".[dev]"
pytest tests/ -q
```

Integration tests use toy fixtures under `tests/fixtures/` (pre-aggregated soft-link years from `scen_corrige_251129_0112`, ~50× smaller than the full `.vd`). Regenerate them with:

```bash
python scripts/build_toy_fixtures.py
```

That writes `toy_scen.*` (coverage / balances) and a leaner `toy_qa.*` (two years, top export-category neighbourhood) for `generate_qa_report` tests. To run against the full scenario instead: `TIMES_PYPSA_FULL_DATA=1 pytest tests/ -q`.

| Test module | Checks |
|-------------|--------|
| `test_topology.py` | `.vdt` parse, IN/OUT counts |
| `test_balances.py` | Comnet residuals, loops API, node residuals |
| `test_extraction_coverage.py` | empty rules allowlist, double-count allowlist, parent–child, tagging |
| `test_aggregation_levels.py` | shared CSV columns, column-based `aggregate_flows`, aliases, unknown level (synthetic / fast) |
| `test_collapse_commodities.py` | commodity hub collapse; expected final-demand / non-PJ sinks vs unexplained imbalance warn/error |
| `test_qa_html.py` | interactive QA HTML, Sankey colours/netting, multi-year report |

Known-zero allowlist: `ammonia`, `methanol`, `total international navigation`, `coal` (expected 0 in the reference scenario; re-check on a new one).

#### Open points

Points of attention for the reference scenario; the full list with context is in
[aggregation.md § Open points](aggregation.md#open-points-and-points-of-attention).
Re-run `times-pypsa qa` after any mapping/rule change and re-read the QA tables.

- **Aviation kerosene FIN-vs-FOUT** — aviation is exported as the service `VAR_FOut`; the ~30 PJ kerosene `VAR_FIn` stays out to avoid double-counting. Confirm the convention matches PyPSA.
- **Comnet residuals** — some PJ carriers disagree with `VAR_Comnet` (trade / stock / IMPEXP terms outside process flows); see `qa_node_balance_*.csv`.
- **Surviving loops after netting** — industry heat/steam, steel electric-furnace, and bio/CHP/electricity clusters; physical recycles or aggregation artefacts (`qa_loops_*.csv`).
- **Commercial electricity** is soft-linked as a single `total electricity services` total (cooling / lighting / appliances not split).
- **`total international navigation`** is always 0 — its filter label `international navigation` is absent from the mappings and the category is allowlisted as known-zero; correct the label if it should be non-zero.

#### Rule-change log

| Date | Change | Evidence | Demand CSV impact |
|------|--------|----------|-------------------|
| 2026-07-24 | **Export strategy**: exported Sankey links coloured by PyPSA sector (Industry/Transport/Residential/Services/Agriculture) and anchored to the demand inflow (`assign_export_sectors`); retired single-blue/light-red-mixed scheme (mixed verified absent). | `test_export_sectors.py`, `output/qa_custom/`, [aggregation.md](aggregation.md#export-strategy-per-sector-colours--demand-inflow-anchoring) | None (display only; CSVs byte-identical 2021–2050) |
| 2026-07-24 | **Coverage-gap fixes**: added `residential cooking` rule (uncaptured demand, ~1.4→4.6 PJ/yr); fixed biofuel-blending double-count in `total road` (`road_internal_transfer_pj`). | `test_export_sectors.py`, `test_extraction_coverage.py` | **`total road` −5 PJ (2030) → −0.3 PJ (2050); new `residential cooking` category. No other category changes (byte-diff, all years).** |
| 2026-07-18 | Introduced QA toolkit; canonical filter column `process_agg` (legacy `agg_level_1` alias). Allowlist: `total rail`⊃`electricity rail`; known-zero `coal`. No `extraction_rules.csv` edits. | `output/qa_2050/` | None |
| 2026-07-18 | Sankey HTML: whole-system view + multi-year timeline + netting toggle on all Sankeys; `--year` optional (defaults to all model years). | `output/qa/` | None |
| 2026-07-18 | Column-based Sankey aggregation (`--agg-level`); shared CSV columns including `sankey_overview` (≤20 nodes). Commodities gained `Aggregation Level 1/2` aligned with process headers. | mappings + `test_aggregation_levels.py` | None |
| 2026-07-18 | Sankey: collapse balanced commodity hubs to process→process flows; residual imbalance → magenta `U ·` artificial nodes named like the warning; link colours blue/mixed/grey for PyPSA export status. | `collapse_commodity_nodes` + `test_collapse_commodities.py` | None |
| 2026-07-19 | `custom` aggregation: individualize exported processes/commodities (Description labels) so netted export rows are pure blue; document structural mixed links after commodity collapse. | `mapping_*.csv` `custom` + [aggregation.md](aggregation.md) | None |
| 2026-07-19 | Sankey process nodes green (imbalance nodes stay magenta) so they do not clash with blue exported links; aggregation docs moved to `aggregation.md`. | `sankey_html.py` + README | None |
| 2026-07-19 | Collapse colouring: exported+context → blue (any-exported); diesel import→fuel-tech and heat→buildings now blue. | `merge_export_statuses(any_exported=True)` + [aggregation.md](aggregation.md) | None |
| 2026-07-19 | Plotly has no Sankey link gradients; purple = both FOut+FIn exported (`double_count`). | `DOUBLE_COUNT_COLOR` + `collapse_pair_export_status` | None |
| 2026-07-19 | Expected Sankey sinks (FOut-only DEM / non-PJ consumers): magenta `U ·` named after the demand process, INFO log + tooltip; unexplained imbalances still warn/error. | `collapse_commodity_nodes` + `test_collapse_commodities.py` | None |
| 2026-07-19 | Default Sankey link threshold **0** (was ≈1 PJ); `custom` bundles household/commercial electrical appliances and building retrofits. | `units.py` + `mapping_*.csv` `custom` | None |
| 2026-07-19 | Aggressive readable `custom`: keep export-touching Aggregation Level 2, collapse context to overview/`(other)`/`(context)`; ~45 nodes while preserving blue soft-link mass. | `refine_custom_labels_for_readability` + mappings | None |
| 2026-07-20 | Supply-chain split (Local production / Imports / Fuel refining / Power plants); post-collapse reciprocal netting; Sankey netting-toggle uses Plotly.newPlot. | mappings + `net_collapsed_process_links` + `sankey_html.py` | None |
| 2026-07-21 | Map `BATELCIN`; split `Electricity`/`Oil products`/`Biomass` context hubs (`BATELCOUT`, `INDELC`, `OILDST`, kerosene, `BIOCPS`); fuel-tech display kerosene/oil/diesel. | Sankey artifacts TO→Industry, naphtha↔diesel, PP→wood chips; [aggregation.md](aggregation.md) | None (extraction already counted `TRA_STG`×`BATELCIN`) |
| 2026-07-22 | Sankey: Fuel refining→Fuel conversion; Biogas production (+`BWSUPGZH100`/`BIOSLUH`); grid/pumped/home storage nodes; split HV vs residential electricity. | Biogas≈8 TWh Wallonia; storage charge/discharge | None |
| 2026-07-21 | Sankey: split commercial other (cooling/lighting/appliances) + Residential/Commercial buildings; map `CLIG*`/`COEL`; fix `TNAINLBDLN01`→domestic navigation. | Notes appliances→Buildings / HX→Buildings; nav looked missing | None (L2/rules unchanged; nav already exported) |
