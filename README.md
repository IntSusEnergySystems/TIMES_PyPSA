### TIMES_PyPSA

Soft-linking between the TIMES-WAL and PyPSA-WAL models. This repository provides the **`times_pypsa`** Python package to parse TIMES `.vd` output files, extract PyPSA demand categories, generate interactive Sankey energy-flow diagrams, and run multi-view extraction quality assurance.

Related: [SOFTLINKING_ANALYSIS.md](SOFTLINKING_ANALYSIS.md) for architecture, coupling workflow, verification, and open modeller questions. [aggregation.md](aggregation.md) for Sankey aggregation levels, and export colouring limits.
Important note: when testing, and troubleshooting, always use the 'custom' aggregation level.

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

Omit `--year` to include all model years in the interactive HTML report (year slider + flow-netting toggle on each Sankey). Optional: `--year 2050`, `--year 2030,2040,2050`, or `--year 2025-2050`. Energy values default to **TWh** (`--units pj` for petajoules). `--threshold-export` is interpreted in the selected unit (default ≈ 1 PJ). `--agg-level` selects the shared mapping CSV column used for process × commodity node labels (default: `Aggregation Level 2`). See [Extraction QA](#extraction-qa) and [aggregation.md](aggregation.md).

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

#### Status (direct answers)

| Question | Answer |
|----------|--------|
| Are current extraction rules adequate? | **Partially.** Parent–child heat identities match; no disallowed double-counting after the rail allowlist; topology is clean. But DMD coverage gaps remain large (~330 PJ in 2050), so we cannot claim “no forgotten demand” without TIMES-expert confirmation (see [Questions for TIMES experts](#questions-for-times-experts)). |
| Former aggregation rules still active? | **Yes.** Extraction still uses `mapping_processes.csv` **Aggregation Level 2** (column `process_agg`), `mapping_commodities.csv` **PyPSA Energy Carrier**, and `extraction_rules.csv` unchanged in formalism. |
| New aggregation rules / new CSV formalism? | **Shared column names.** Any aggregation level is a column present in **both** mapping CSVs; `--agg-level` / `aggregate_flows(level=...)` selects it. Bundled levels: `Sector`, `Aggregation Level 1`, `Aggregation Level 2`, `custom` (export-individualized working level), `sankey_overview`. Details: [aggregation.md](aggregation.md). Alias `mapping` ≡ `Aggregation Level 2`. |
| What changed in the Sankey? | **Commodity hubs collapsed** to process→process flows (commodities are link labels). Expected final-demand / non-PJ residuals become magenta `U ·` nodes named after the demand process (not logged as errors); unexplained imbalances keep `Unbalanced …` labels and error/warn. Process nodes are **green**. Whole-system + export-neighbourhood views; multi-year timeline + netting toggle; `sankey_overview` (~17 process labels before collapse). |

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
| **`custom`** | Working level: individualize PyPSA exports (blue under netting) |
| `Aggregation Level 1` / `Sector` / `L2` | Mid / coarse / fine drill-down |
| **`sankey_overview`** | Whole-system Sankey (≤20 nodes) |

**No opaque placeholders.** Empty mapping cells fall back to TIMES Description, then code — never `Unknown`.

Full documentation (how to add levels, commodity-hub collapse, node/link colours, `custom` design, and remaining limits) lives in **[aggregation.md](aggregation.md)**.

##### Quick colour legend

| Element | Colour |
|---------|--------|
| Process nodes | green |
| Imbalance residual nodes | magenta (`U ·`; expected sinks use process name) |
| Link exported / mixed / context / double-count | blue / light red / grey / purple |

After commodity collapse, a link is **blue if exactly one endpoint is exported**, **purple if both FOut and FIn are exported**. Plotly cannot gradient-colour a link left/right. See [aggregation.md](aggregation.md#collapse-colouring-rule-any-exported).

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

Known-zero allowlist (SOFTLINKING Q3): `ammonia`, `methanol`, `total international navigation`, `coal`.

#### Questions for TIMES experts

Filled from QA run on `scen_corrige_251129_0112`, year **2050** (`output/qa_2050/`). Re-run `times-pypsa qa` after mapping changes.

1. **Comnet residuals on energy commodities.** After restricting to commodities with FIn/FOut activity, some PJ carriers still disagree with `VAR_Comnet`. Are trade / stock / IMPEXP terms expected outside process flows? See `qa_node_balance_2050.csv` failures.

2. **DMD coverage gaps.** Unexported end-use (`Type=DMD`) FIN flows — see `qa_coverage_gap_2050.csv`. Notable examples in this scenario:
   - `international aviation` × Kerosenes (~31 PJ FIN) while the soft-link rule uses `VAR_FOUT` — confirm FIN vs FOUT choice.
   - `Cars` × `BATELCOUT` (~23 PJ) — battery discharge into EVs; grid electricity is already taken via `Fuel Tech - Electricity (TRA)` + `TRA_STG`. Confirm this should stay excluded (avoid double count).
   - `Buildings: built area` × Heat — large RSD heat-like flows not in residential boiler / BEWAL heat rules. Soft-link intentionally, or missing?

3. **Persistent zeros.** Confirm these stay absent (allowlisted): `ammonia`, `methanol`, `total international navigation`, `coal` (no industry hard-coal/lignite FIN in soft-link years for this scenario). Many residential coal/biomass/solar/oil boiler categories are also 0 in 2050 but non-zero in earlier years — OK.

4. **Topology mismatches.** After ignoring `process_code='-'` (GHG aggregates), **this scenario has zero mismatches**. Re-check if a new `.vd`/`.vdt` pair starts reporting rows in `qa_topology_mismatches_*.csv`.

5. **Commodity hub imbalance after `sankey_overview` collapse (2050).** Intermediate carriers that mix grid fuels with final-demand commodities (FOUT-only end-uses such as aviation fuels `TAIF`/`TAIP`, non-energy `NEO`, residential/commercial appliance electricity `ROEL`/`COSE`/…) show ΣFOut ≫ ΣFIn at the aggregated label. Pure FOut-only hubs are now treated as expected demand sinks (magenta `U ·` named after the process, no error). Mixed clusters (e.g. overview `Oil products` / `Electricity`) can still show unexplained residuals — confirm whether those end-use commodities should stay out of the carrier clusters (or be mapped to a dedicated “Final demand” overview label).

6. **Surviving loops after netting** (`qa_loops_2050.csv`):
   - Industry heat/steam cycle (`INDHET` / `INDHTH` / …)
   - Steel scrap/electric furnace cluster
   - Large bio/CHP/electricity cluster (`ELCHIG`, `INDELC`, …)
   Are these physical recycles / CHP feedbacks, or aggregation artifacts?

7. **`process_agg` label typos.** Any Aggregation Level 2 labels in `extraction_rules.csv` that do not exist in `mapping_processes.csv`? (Automated check recommended as follow-up.)

8. **Services district heating vs BEWAL services heat.** In 2050, BEWAL services urban decentral heat is only ~0.25 PJ (mostly gas boiler). Is commercial heat demand that low in this scenario, or are district / other techs mis-mapped?

#### Rule-change log

| Date | Change | Evidence | Demand CSV impact |
|------|--------|----------|-------------------|
| 2026-07-18 | Introduced QA toolkit; canonical filter column `process_agg` (legacy `agg_level_1` alias). Allowlist: `total rail`⊃`electricity rail`; known-zero `coal`. No `extraction_rules.csv` edits. | `output/qa_2050/` | None |
| 2026-07-18 | Sankey HTML: whole-system view + multi-year timeline + netting toggle on all Sankeys; `--year` optional (defaults to all model years). | `output/qa/` | None |
| 2026-07-18 | Column-based Sankey aggregation (`--agg-level`); shared CSV columns including `sankey_overview` (≤20 nodes). Commodities gained `Aggregation Level 1/2` aligned with process headers. | mappings + `test_aggregation_levels.py` | None |
| 2026-07-18 | Sankey: collapse balanced commodity hubs to process→process flows; residual imbalance → magenta `U ·` artificial nodes named like the warning; link colours blue/mixed/grey for PyPSA export status. | `collapse_commodity_nodes` + `test_collapse_commodities.py` | None |
| 2026-07-19 | `custom` aggregation: individualize exported processes/commodities (Description labels) so netted export rows are pure blue; document structural mixed links after commodity collapse. | `mapping_*.csv` `custom` + [aggregation.md](aggregation.md) | None |
| 2026-07-19 | Sankey process nodes green (imbalance nodes stay magenta) so they do not clash with blue exported links; aggregation docs moved to `aggregation.md`. | `sankey_html.py` + README | None |
| 2026-07-19 | Collapse colouring: exported+context → blue (any-exported); diesel import→fuel-tech and heat→buildings now blue. | `merge_export_statuses(any_exported=True)` + [aggregation.md](aggregation.md) | None |
| 2026-07-19 | Plotly has no Sankey link gradients; purple = both FOut+FIn exported (`double_count`). | `DOUBLE_COUNT_COLOR` + `collapse_pair_export_status` | None |
| 2026-07-19 | Expected Sankey sinks (FOut-only DEM / non-PJ consumers): magenta `U ·` named after the demand process, INFO log + tooltip; unexplained imbalances still warn/error. | `collapse_commodity_nodes` + `test_collapse_commodities.py` | None |
