### TIMES_PyPSA

Soft-linking between the TIMES-WAL and PyPSA-WAL models. This repository provides the **`times_pypsa`** Python package to parse TIMES `.vd` output files, extract PyPSA demand categories, generate interactive Sankey energy-flow diagrams, and run multi-view extraction quality assurance.

Related: [SOFTLINKING_ANALYSIS.md](SOFTLINKING_ANALYSIS.md) for architecture, coupling workflow, verification, and open modeller questions.

### Install

From the repository root:

```bash
python -m pip install --upgrade pip
pip install -e .
```

Dependencies: `pandas`, `plotly` (Python 3.9+).

Bundled mapping files live in `times_pypsa/mappings/` (`mapping_commodities.csv`, `mapping_processes.csv`, `extraction_rules.csv`).

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

Coarse overview report (≤20 Sankey nodes; see [Custom aggregation levels](#custom-aggregation-levels)):

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

Omit `--year` to include all model years in the interactive HTML report (year slider + flow-netting toggle on each Sankey). Optional: `--year 2050`, `--year 2030,2040,2050`, or `--year 2025-2050`. Energy values default to **TWh** (`--units pj` for petajoules). `--threshold-export` is interpreted in the selected unit (default ≈ 1 PJ). `--agg-level` selects the shared mapping CSV column used for process × commodity node labels (default: `Aggregation Level 2`). See [Extraction QA](#extraction-qa) and [Custom aggregation levels](#custom-aggregation-levels).

Use `--mappings-dir` to override the bundled mappings (defaults to the package `times_pypsa/mappings/`).

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
    mappings_dir="times_pypsa/mappings",
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
    mappings_dir="times_pypsa/mappings",
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
- `mapping_commodities.csv`, `mapping_processes.csv`: TIMES → PyPSA mappings (also shipped in the package)

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
| New aggregation rules / new CSV formalism? | **Shared column names.** Any aggregation level is a column present in **both** mapping CSVs; `--agg-level` / `aggregate_flows(level=...)` selects it. Bundled levels: `Sector`, `Aggregation Level 1`, `Aggregation Level 2`, `custom` (editable working copy of Level 2), `sankey_overview`. Alias `mapping` ≡ `Aggregation Level 2`. |
| What changed in the Sankey? | Added **whole-system** energy-flow Sankey; export neighbourhood (n−1/n+1) retained. All Sankeys are **multi-year** with a timeline slider and **flow-netting toggle** (standalone HTML/JS, not SEPIA). Coarse `sankey_overview` level (~17 nodes) for readable whole-system views. |

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

| Level (`--agg-level`) | Process nodes | Commodity nodes | Typical node count | Use |
|-----------------------|---------------|-----------------|--------------------|-----|
| **`Aggregation Level 2`** (default; alias `mapping`) | Aggregation Level 2 | Aggregation Level 2 (= PyPSA Energy Carrier) | hundreds | Export neighbourhood / detailed QA |
| **`custom`** | Editable copy of Level 2 (plus filled gaps) | Editable copy of Level 2 | ~Level 2 | **Working level** for modeller-driven clustering |
| `Aggregation Level 1` | Aggregation Level 1 | Aggregation Level 1 (= Cluster) | high | Mid drill-down |
| `Sector` (alias `L0`) | Sector codes | Sector codes | ~15 | Coarse sector check |
| **`sankey_overview`** | Overview process clusters | Overview carriers | **≤20** | Readable whole-system Sankey |
| `L2` | `process_code` | `commodity_code` | thousands | Fine drill-down (no collapse) |

**No opaque placeholders.** Empty mapping cells fall back to TIMES Description, then code — never `Unknown`.

#### Custom aggregation levels

Sankey readability is controlled by mapping CSVs, not by hard-coded Python clusters. To define a new level:

1. **Pick one column name** (e.g. `sankey_overview`, `my_sector_v2`). The same header must exist in:
   - `times_pypsa/mappings/mapping_processes.csv`
   - `times_pypsa/mappings/mapping_commodities.csv`
2. **Fill every row** with the display label for that process or commodity. Empty cells are **not** shown as `Unknown`: the aggregator falls back to the TIMES **Description**, then the TIMES **code** (from the flow row / `data/AllProcesses.csv` / `data/AllCommodities.csv`). For `sankey_overview`, empty cells are first inferred into the overview clusters using description/code heuristics, then fall back to Description/code only if no cluster matches.
3. **Pass the column name** as `--agg-level my_column` (CLI) or `aggregate_flows(..., level="my_column")` / `PipelineConfig(agg_level="my_column")`.
4. **Check node budget.** After aggregation, unique process labels + unique commodity labels = total Sankey nodes. For an overview diagram, aim for **≤20 total**. Mid-level views can be larger; the default Level 2 view is intentionally detailed.
5. **Never invent opaque buckets** such as `Unknown` / `Unmapped: …`. If a code is missing from the mapping CSVs, its Sankey name must still be understandable from the TIMES description or code.

Which columns count as levels? Any header present in **both** CSVs except identity/metadata (`Process`, `TIMES commodity`, `Description`, `Type`, units, upstream fields, …). Helpers: `shared_aggregation_columns(processes_df, commodities_df)`.

```python
from times_pypsa import load_times_annual_flows, aggregate_flows, default_mappings_dir

model = load_times_annual_flows("scen.vd", default_mappings_dir())
flows = model.energy_flows(2030)
agg = aggregate_flows(flows, level="sankey_overview", apply_netting=True)
n_proc = agg["process_code"].nunique()
n_com = agg["commodity_code"].nunique()
print(n_proc, n_com, n_proc + n_com)  # keep the sum small for overview plots
```

##### Process vs commodity nodes (Plotly)

Plotly Sankey diagrams do not support different node shapes. This package therefore uses two simultaneous cues:

1. **Label prefix:** `P · …` for processes, `C · …` for commodities  
2. **Colour family:** processes in a **blue** palette, commodities in an **amber** palette (each node still gets its own shade within the family)

Internal node ids are typed (`process::Label` / `commodity::Label`) so a process and a commodity that share the same name never collapse into one Plotly node. Link colours still encode export status (blue / grey / light red).

##### Designing `custom` (working level)

`custom` is seeded as a copy of **Aggregation Level 2** on both mapping CSVs. Processes that were missing from the mapping were added with clustered labels inferred from TIMES descriptions (heat pumps, CHP, retrofits, PV, industry, H₂, …) so the column has **no empty / Unknown** cells. Edit `custom` freely in:

- `times_pypsa/mappings/mapping_processes.csv`
- `times_pypsa/mappings/mapping_commodities.csv`

then re-run QA with `--agg-level custom`. Use `qa_sankey_label_map_{year}.csv` to see which TIMES codes sit under each custom label.

##### Designing `sankey_overview` (worked example)

Goal: a whole-system Sankey with **at most ~20 nodes**, still reflecting the main energy story (supply → conversion → end use).

**Process side (9 labels)** — cluster by role, not by TIMES sector alone:

| Label | Criterion |
|-------|-----------|
| Imports & trade | `Sector=IMP` or Aggregation Level 2 contains import/export |
| Power plants | Electricity sector generation (not fuel-tech PRE) |
| CHP & district heat | `Type=CHP` or district-heating / CHP labels |
| Fuel supply | `Sector=SUP` |
| Fuel conversion | “Fuel Tech …” processes in end-use sectors / ELC |
| Industry / Buildings / Transport / Agriculture | Remaining DMD/PRE activity by sector (`RSD`+`COM` → Buildings) |

**Commodity side (8 labels)** — cluster by energy carrier family (keywords on carrier / cluster / code):

| Label | Examples |
|-------|----------|
| Electricity | grid electricity, hydro/wind/nuclear power carriers |
| Gas | natural gas, network gas, biogas |
| Oil products | diesel, gasoline, kerosene, LPG, fuel oil, navigation/aviation fuels |
| Coal & solids | coal, coke, lignite |
| Heat | heat, geothermal, solar thermal |
| Biomass & biofuels | wood, biodiesel, ethanol, black liquor, wastes |
| Hydrogen | H₂ carriers |
| Other | residual non-energy / accounting (e.g. degree-day corrections) |

That yields **17 distinct labels** in the bundled mappings (9 process + 8 commodity). Criteria that worked well:

- Prefer **physical role** (import, generate, convert, consume) over raw TIMES sector codes for processes.
- Prefer **carrier family** over sector-specific commodity names (`gas for industry` and `Network gas for Residential` → both `Gas`).
- Keep a small **Other** bucket for true leftovers; if Other grows, split or reassign before adding new top-level labels.
- Re-count unique labels after editing: `df["sankey_overview"].nunique()` on each CSV, then sum.

##### Practical tips

- Edit the **package** mappings under `times_pypsa/mappings/` (used by default). Keep `data/mapping_*.csv` in sync if you use that copy.
- Do **not** invent Sankey display prefixes in code (`Unknown`, `Unmapped: …`); put the intended label in the CSV, or rely on TIMES Description/code fallbacks.
- Full TIMES dictionaries for names: `data/AllProcesses.csv` and `data/AllCommodities.csv` (semicolon-separated VEDA exports). The loader uses these to fill missing process/commodity descriptions on flows.
- Extraction rules are independent: they still filter on Aggregation Level 2 / `process_agg`. Changing `sankey_overview` does not change PyPSA demand exports.
- Legacy aliases: `L0`→`Sector`, `L1`→`Aggregation Level 1`, `mapping`→`Aggregation Level 2`, `L2`→raw codes.

##### Mapping Sankey nodes back to TIMES codes

When debugging a Sankey node, use the QA crosswalk CSV (written next to the HTML report):

- **`qa_sankey_label_map_{year}.csv`** — for each aggregated Sankey label, lists the original TIMES `times_code` / `times_description` members (process or commodity), the raw mapping CSV label (`mapping_label`, empty when inferred), and the contributing energy (`value` in the selected unit).

Related flow-level files (same `out-dir`):

- **`qa_flows_{year}.csv`** — annual VAR_FIn/VAR_FOut rows with original `process_code` / `commodity_code` / descriptions, export tags, and carriers (before Sankey collapse).
- **`qa_export_neighborhood_{year}.csv`** — neighbourhood subset used for export-focused Sankeys (still at TIMES code resolution).

Example: to see which TIMES processes sit under `Buildings` after `--agg-level sankey_overview`:

```bash
python -c "import pandas as pd; m=pd.read_csv('output/qa_overview/qa_sankey_label_map_2030.csv'); print(m.query(\"side=='process' and sankey_label=='Buildings'\")[['times_code','times_description','TWh']].head(20))"
```

##### Export neighbourhood (n−1 / n / n+1)

1. Core = process and commodity codes on flows matched by extraction rules
2. Keep every energy flow that shares a core process **or** core commodity
3. That includes upstream producers / other inputs (n−1) and downstream consumers / other outputs (n+1)
4. Aggregate with `mapping` labels; colour blue = exported, grey = context

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
- `qa_sankey_label_map_{year}.csv` — **Sankey label → TIMES code crosswalk** (see [Mapping Sankey nodes back to TIMES codes](#mapping-sankey-nodes-back-to-times-codes))
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
| Category definitions / filters | `times_pypsa/mappings/extraction_rules.csv` |
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

5. **Surviving loops after netting** (`qa_loops_2050.csv`):
   - Industry heat/steam cycle (`INDHET` / `INDHTH` / …)
   - Steel scrap/electric furnace cluster
   - Large bio/CHP/electricity cluster (`ELCHIG`, `INDELC`, …)
   Are these physical recycles / CHP feedbacks, or aggregation artifacts?

6. **`process_agg` label typos.** Any Aggregation Level 2 labels in `extraction_rules.csv` that do not exist in `mapping_processes.csv`? (Automated check recommended as follow-up.)

7. **Services district heating vs BEWAL services heat.** In 2050, BEWAL services urban decentral heat is only ~0.25 PJ (mostly gas boiler). Is commercial heat demand that low in this scenario, or are district / other techs mis-mapped?

#### Rule-change log

| Date | Change | Evidence | Demand CSV impact |
|------|--------|----------|-------------------|
| 2026-07-18 | Introduced QA toolkit; canonical filter column `process_agg` (legacy `agg_level_1` alias). Allowlist: `total rail`⊃`electricity rail`; known-zero `coal`. No `extraction_rules.csv` edits. | `output/qa_2050/` | None |
| 2026-07-18 | Sankey HTML: whole-system view + multi-year timeline + netting toggle on all Sankeys; `--year` optional (defaults to all model years). | `output/qa/` | None |
| 2026-07-18 | Column-based Sankey aggregation (`--agg-level`); shared CSV columns including `sankey_overview` (≤20 nodes). Commodities gained `Aggregation Level 1/2` aligned with process headers. | mappings + `test_aggregation_levels.py` | None |
