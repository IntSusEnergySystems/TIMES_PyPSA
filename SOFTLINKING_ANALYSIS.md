# TIMES → PyPSA soft-linking — ecosystem analysis & unification strategy

**Author:** automated analysis (Cursor agent)
**Date:** 2026-07-18 (consolidated from 2026-07-06 / 2026-07-17 drafts)
**Status (Part II):** design only — **nothing implemented**. Review with TIMES
and PyPSA specialists before coding.
**Repos analysed:**
- `/home/sylvain/svn/TIMES_PyPSA` (soft-linking library, branch `pypsa-extraction-improvements`)
- `/home/sylvain/svn/pypsa-wal` (PyPSA-Eur based Walloon model, run `walloon-model`)
- `/home/sylvain/svn/climact-pypsa-eur_results_extraction-88d352b59aa4` (post-solve Explorer CSV extraction)

**Part I** (§0–§12) documents the current state: how the three repositories
relate, whether `times-pypsa` is used by PyPSA-Wal, Sankey / energy-flow
parallels, empirical run results, gaps, and overlaps.

**Part II** (§13–§20) specifies the target automated workflow, design decisions,
implementation phases, risks, and open questions.

> **TL;DR — three findings.**
>
> 1. **Demand extraction is centralised in `times_pypsa`.** The pip package
>    (`times_pypsa/pipeline.py`) is the single implementation. PyPSA-Wal calls it
>    via a thin Snakemake wrapper; standalone Sankey/QA uses the same code
>    (`times-pypsa` CLI or `scripts/bau_sankey_diagram.py`).
>
> 2. **ClimAct is a separate downstream stage.** It reads **solved PyPSA `.nc`**
>    networks only (pypsa 0.35.x / `datapypsa` env). It does **not** parse `.vd`
>    files. TIMES data reaches the Wallonie Explorer as a **raw `.vd` upload** in
>    `scenarios/…/times/`.
>
> 3. **Sankey / energy-flow logic exists in three places** with the same *purpose*
>    (aggregated energy-system views for charts) but different *sources* and
>    *formats* — see §6. TIMES_PyPSA builds explicit commodity↔process link
>    tables + Plotly HTML; ClimAct builds carrier×sector pivot tables for Explorer;
>    pypsa-wal's in-workflow fork duplicates the TIMES side. These mapping tables
>    (`mapping_processes.csv` vs `sector_mapping.csv`) are **not shared** and serve
>    different model ontologies (TIMES processes vs PyPSA components).

---

## 0. Three repositories — roles and data flow

```
TIMES / VEDA
    └── scenario.vd
            │
            ├─► [TIMES_PyPSA]  times_pypsa package  (library + CLI + standalone Sankey)
            │       └── export / sankey → demands CSVs, HTML, flow tables
            │
            └─► [pypsa-wal]  build_wallon_demands.py  (thin Snakemake wrapper)
                    └── wallon_demands_{h}.csv  ──► prepare_sector_network.py …
                            │
                            └── Snakemake solve ──► results/…/networks/*.nc
                                    │
                                    └─► [ClimAct extraction]  graph_extraction_main.py
                                            └── 49 pypsa/*.csv + strategy/*.csv
                                                    │
                                                    └── S3 scenarios/…/pypsa|strategy|times/
                                                            └── Wallonie Explorer
```

| Repo | Stage | Primary input | Primary output | Used by pypsa-wal? |
|------|-------|---------------|----------------|-------------------|
| **TIMES_PyPSA** | Pre-solve soft-linking | `.vd` + mapping CSVs | Demand CSVs, Sankey HTML, flow tables | **Yes** (via `times_pypsa` pip package) |
| **pypsa-wal** | Build + optimise | `.vd` via `times_pypsa` + hand-maintained CSVs | Solved `.nc`, summary CSVs, plots | — |
| **ClimAct extraction** | Post-solve publishing | Solved `.nc` | Explorer-ready CSVs | **No** (manual step) |

**PyPSA version split:** pypsa-wal uses **pypsa 1.x** via the `pypsa-eur` conda
env. ClimAct requires **pypsa 0.35.x** (`datapypsa` env). These cannot be merged
into a single Python package without a major PyPSA upgrade on one side.

---

## 1. Is `times-pypsa` used by pypsa-wal?

**No — not as a library dependency.**

PyPSA-Wal **does** call the `times_pypsa` package (since 2026-07-18): Snakemake rule
`build_wallon_demands` runs a **~40-line wrapper**
(`scripts/build_wallon_demands.py`) that delegates to
`times_pypsa.export_horizon()`. The ~1 100-line inline parser was removed.

Installation: editable pip dependency in `envs/environment.yaml`
(`pip install -e ../TIMES_PyPSA`, sibling checkout). Mapping CSVs default to
`data/walloon/` via `sector.times_mappings_dir`; bundled copies ship inside
`times_pypsa/mappings/` when that config key is omitted.

Previously (before this change), pypsa-wal duplicated the full extractor in
`build_wallon_demands.py` without importing TIMES_PyPSA.

---

## 2. How `.vd` → PyPSA input works in pypsa-wal

When `sector.times_demand: true`:

| Step | Script / rule | What happens |
|------|---------------|--------------|
| 1 | `build_wallon_demands` | Parse `.vd` → aggregate timeslices → apply extraction rules → write `wallon_demands_{h}.csv` |
| 2 | `build_population_weighted_energy_totals` | Overwrite BEWAL row in nodal energy totals from `wallon_demands` |
| 3 | `build_transport_demand`, `build_industrial_energy_demand_per_node` | Sector-specific nodal demands |
| 4 | `prepare_sector_network` | Heat, EV, shipping, aviation loads; special BEWAL efficiency handling |

**Extractor mechanics** (shared with TIMES_PyPSA — see §4):

- Parse `.vd` lines → columns `[year, region, timeslice, variable, commodity_code, process_code, value]`
- Aggregate timeslices to annual PJ
- Map commodities/processes via `mapping_commodities.csv` / `mapping_processes.csv`
- Apply category rules from `extraction_rules.csv` (54 categories in pypsa-wal)
- Net internal transfers (`net_bidirectional_links`) within aggregated process groups
- Convert PJ → TWh (`× 0.277778`)
- Post-process: subtract `electricity rail` from `electricity road`, `total rail` from `total road`

**Road/rail netting is triplicated:** in the TIMES extractor post-processing,
again in `build_transport_demand.py` when `times_demand: true`, and implicitly
in how PyPSA-Eur would handle road+rail without TIMES.

---

## 3. What was run (July 2026 baseline audit)

Both tools were run in the **same conda environment** (`pypsa-eur`), confirming
the single-environment goal for demand extraction is feasible:

| Package | Version in `pypsa-eur` |
|---------|------------------------|
| Python  | 3.13.13 |
| pandas  | 2.3.3 |
| plotly  | 6.6.0 |

The main script (`scripts/bau_sankey_diagram.py`) has the input `.vd` and the
Sankey year **hard-coded** in `main()`. It must be run from the **repository
root** (paths are relative to CWD: `data/…`, `output/…`), not from `scripts/` as
the `README.md` suggests.

```bash
cd /home/sylvain/svn/TIMES_PyPSA
TIMES_VD_FILE="data/scen_base_coherence_3110.vd" TIMES_SELECTED_YEAR=2030 \
  conda run -n pypsa-eur python scripts/bau_sankey_diagram.py
```

Both demo (`bau_080925_0809.vd`) and baseline runs completed without error (~6–7 s
each). Generated CSVs preserved under `output/runs/{demo,baseline}/`.

---

## 4. What TIMES_PyPSA produces

For one `.vd` file, one run of `bau_sankey_diagram.py` writes to `output/`:

| File | Cardinality | Content | Consumed by PyPSA-Wal? |
|------|-------------|---------|------------------------|
| `pypsa_demands_{year}.csv` | one per year in `.vd` | `category, TWh, PJ` — 53 categories | **No** (pypsa-wal re-derives `wallon_demands_{h}.csv`) |
| `heating_capacities_{selected_year}.csv` | one (selected year only) | heating stock MW | **No** — see §11.1 |
| `annual_values{_clustered}.csv` | one | full long table of all variables | No (intermediate) |
| `annual_flows_{year}_energy{_clustered}.csv` | one | filtered Sankey link table | No (visualisation / QA) |
| `bau_sankey_{year}_pj{_clustered}.html` | one | Plotly interactive Sankey | No (visualisation / QA) |
| `sankey_commodity_groups_{year}.{csv,json}` | one each | commodity grouping metadata | No (visualisation / QA) |

**Key mechanics of demand CSVs:**

- Only `VAR_FIn` / `VAR_FOut` used; PJ → TWh with `0.277778`.
- Categories filter on `Aggregation Level 2` (process) and `PyPSA Energy Carrier` (commodity).
- Netting removes internal transfers inside aggregated process groups.
- Road/rail subtraction applied silently after the rule loop.

**Dead code:** `scripts/extract_pypsa_demands.py` is an older superseded extractor
(reads `annual_values_{year}.csv` + non-existent `pypsa_mapping.csv`). Should be
removed or archived.

---

## 5. ClimAct extraction repo

**Location:** `climact-pypsa-eur_results_extraction-88d352b59aa4`

**Purpose:** Extract metrics from **solved PyPSA-Eur networks** into CSVs consumed
by the [Wallonie Explorer](https://explorer.test.wallonie.climact.com/) Streamlit
app. Completely **post-solve** — no role in the Snakemake build chain.

**Pipeline** (`scripts/graph_extraction_main.py`):

1. `extract_data` — load `.nc` networks (local symlink or S3)
2. `transform_data` — extract intermediate CSVs to `analysis/graph_data/`
3. `load_data_st` — reformat into 49 Streamlit-ready CSVs under `analysis/graph_extraction_st/`
4. `load_strategy_metrics` — derive strategy indicators from PyPSA CSVs via `strategy_metrics_mapping.csv`

**What it does NOT do:**

- No `.vd` parsing
- No Plotly Sankey HTML generation (no `go.Sankey` / `plotly` usage in the repo)
- No consumption of `wallon_demands_*.csv` or TIMES_PyPSA outputs

**TIMES on Explorer:** the TIMES tab is fed by uploading the **raw `.vd`** to
`s3://…/scenarios/<label>/times/` (see `pypsa-wal/times_data_extraction.md`).
The Explorer app parses/renders it client-side; this repo is not involved.

**Operational coupling:** documented in `pypsa-wal/instructions.md` — run
ClimAct extraction in `datapypsa` env after solve, copy CSVs to
`results/walloon-model/explorer/`, then `./cluster/nic5.sh upload`.

Some legacy `times-pypsa` scenarios on S3 also have pre-computed CSVs under
`strategy/report/` (e.g. `demande_par_secteur.csv`); those were generated
**separately at ClimAct**, not by this extraction repo.

---

## 6. Sankey and energy-flow visualisation — three parallel tracks

The ClimAct repo does **not** contain a script named “sankey”, but it implements
the **same architectural pattern** as TIMES_PyPSA's Sankey pipeline: collapse
detailed model entities via mapping tables into chart-ready aggregated energy
flows. The two pipelines are **conceptual mirrors** on opposite sides of the
coupling.

### 6.1 TIMES side — `bau_sankey_diagram.py` / `build_wallon_demands.py`

| Aspect | Detail |
|--------|--------|
| **Source** | TIMES `.vd` export |
| **Granularity** | Explicit **links**: commodity ↔ process (`VAR_FIn` / `VAR_FOut`) |
| **Mapping** | `mapping_processes.csv` → process clustering (`Aggregation Level 2`, `PyPSA technology`) |
| | `mapping_commodities.csv` → commodity grouping (`PyPSA Energy Carrier`, `Cluster`) |
| **Aggregation steps** | Filter PJ energy commodities → cluster processes → group commodities → net bidirectional links |
| **Outputs** | `annual_flows_{year}_energy_clustered.csv` (link table with `clustered_process`, `grouped_commodity` columns) |
| | `bau_sankey_{year}_pj_clustered.html` (Plotly `go.Sankey`: commodity→process for VAR_FIn, process→commodity for VAR_FOut) |
| **Units** | PJ (HTML title); demands also exported as TWh |
| **When run** | Standalone (TIMES_PyPSA) or Snakemake build (pypsa-wal fork) |
| **Represents** | **TIMES model structure** — inputs that drive PyPSA demands |

Example link-table columns (`annual_flows_2030_energy_clustered.csv`):

```
year, region, variable, commodity_code, commodity, process_code, process, value,
clustered_process_code, clustered_process, grouped_commodity_code, grouped_commodity
```

### 6.2 PyPSA results side — ClimAct `graph_extraction_transform.py` / `load_st`

| Aspect | Detail |
|--------|--------|
| **Source** | Solved PyPSA `.nc` networks (`ni.statistics.energy_balance`) |
| **Granularity** | **Aggregated balances**: carrier × sector × node (no explicit link table) |
| **Mapping** | `data/sector_mapping.csv` — maps `(carrier, component, item)` → `sector`, plus `Graph` / `Graph_category` columns for Explorer chart grouping |
| **Aggregation steps** | Energy balance by bus location → merge sector mapping → group by carrier+sector (+ node) → clip small values |
| **Outputs** | `supply_energy_df.csv` — **consumption** by carrier × sector × node × year (TWh) |
| | `production_energy_df.csv` — **production** (same schema, supply-side filter) |
| | `balancing_supply.csv` / `balancing_capacities.csv` — storage/flexibility technologies |
| | `imports_exports.csv`, temporal `load_temporal_*.csv`, `supply_temporal_*.csv` |
| **Units** | TWh (annual), GW (temporal) |
| **When run** | After solve, manual or scripted |
| **Represents** | **Optimised PyPSA outcome** — what the model actually dispatches |

Example Explorer CSV schema (`supply_energy_df.csv`):

```
carrier, sector, node, 2025, 2030, 2040, 2050
```

The `sector_mapping.csv` exported alongside (`sector`, `Graph`, `Graph_category`)
plays the same **ontology-collapse role** as TIMES `mapping_processes.csv`, but
maps PyPSA network components (`AC/generators/onwind` → sector `Onwind`, Graph
`Elec`, Graph_category `Production`) rather than TIMES process codes.

### 6.3 Comparison — why they look related but are not shared code

| Dimension | TIMES_PyPSA Sankey | ClimAct energy-flow CSVs |
|-----------|-------------------|--------------------------|
| Model source | TIMES `.vd` | PyPSA `.nc` |
| Flow representation | Directed links (source→target) | Pivot table (carrier × sector) |
| Mapping file | `mapping_processes.csv`, `mapping_commodities.csv` | `sector_mapping.csv` |
| Visual output | Plotly HTML in-repo | CSV consumed by Explorer (Sankey rendered in Streamlit) |
| Coupling point | Feeds **PyPSA inputs** (demands) | Shows **PyPSA outputs** ( optimised flows) |
| Shared code? | **No** | **No** |
| Shared mapping tables? | **No** — different ontologies | **No** |

**Functional relationship:** for a coupled run, Explorer may show **both**:

- TIMES tab: energy flows from the `.vd` (TIMES modeller's view)
- PyPSA tab: energy flows from `supply_energy_df.csv` / `production_energy_df.csv` (optimiser's view)

Comparing them is a **validation activity**, but today there is no automated
cross-check — the mapping tables are maintained independently.

### 6.4 Additional Sankey in pypsa-wal — SEPIA (fourth system)

`pypsa-wal/SEPIA/` contains its own Plotly Sankey generator
(`SEPIA_functions.py::create_sankey`) producing country HTML reports under
`results/…/htmls/`. This is **unrelated** to TIMES_PyPSA and ClimAct Explorer
CSVs — a legacy Walloon reporting tool with its own node/process ontology.

### 6.5 Duplication within the TIMES Sankey track

The Plotly Sankey code path exists **twice** with the same functions
(`filter_for_sankey`, `build_sankey`, `net_bidirectional_links`, process/commodity
clustering utilities):

- `TIMES_PyPSA/scripts/bau_sankey_diagram.py` (~1 140 lines)
- `pypsa-wal/scripts/build_wallon_demands.py` (~1 120 lines)

These are maintained as copy-paste forks. ClimAct reimplements the *idea* (mapped
aggregation for charts) but not the code or data model.

---

## 7. Demo vs. baseline comparison (demand extraction)

Both scenarios yield the **same category structure** (53 in TIMES_PyPSA, 54 in
pypsa-wal due to `retro`); numbers differ. Baseline (`scen_base_coherence_3110.vd`)
is a coherent decarbonisation pathway distinct from BAU demo.

### 7.1 Baseline — selected TWh per horizon

| Category | 2025 | 2030 | 2040 | 2050 |
|----------|-----:|-----:|-----:|-----:|
| total electricity residential | 4.11 | 3.99 | 3.92 | 4.09 |
| total electricity services | 6.07 | 6.10 | 6.46 | 7.09 |
| electricity (industry) | 8.81 | 7.01 | 9.43 | 10.83 |
| total road | 27.82 | 22.74 | 17.10 | 9.85 |
| electricity road | 0.07 | 0.46 | 3.96 | 4.72 |
| methane | 17.17 | 11.09 | 6.72 | 5.23 |
| BEWAL residential rural heat | 12.46 | 10.77 | 8.05 | 5.18 |
| hydrogen (industry) | 0.00 | 0.00 | 0.22 | 1.36 |

### 7.2 Demo (BAU) vs. baseline at 2030 — selected rows

| Category | demo BAU | baseline | Δ |
|----------|---------:|---------:|--:|
| total road | 34.55 | 22.74 | −11.81 |
| electricity road | 5.42 | 0.46 | −4.96 |
| methane | 13.69 | 11.09 | −2.59 |
| BEWAL residential rural heat | 13.12 | 10.77 | −2.34 |

Full tables: `output/runs/{demo,baseline}/pypsa_demands_*.csv`.

> **Sanity-check flag:** baseline `electricity road` is *lower* than BAU at 2030
> (0.46 vs 5.42 TWh) yet reaches 4.72 TWh by 2050. Combined with road/rail
> netting, confirm with TIMES modeller.

---

## 8. Consistency checks that PASSED

1. **Mapping files byte-identical** between TIMES_PyPSA and pypsa-wal
   (`mapping_commodities.csv`, `mapping_processes.csv`).
2. **Single TIMES region** (`RW` + `NONE`); extractor sums all regions — safe
   today, breaks for multi-region `.vd` without a filter.
3. **Horizon coverage:** `.vd` contains all PyPSA planning years (2025–2050).
4. **Unit conversion** PJ→TWh consistent (`0.277778`) across both extractor forks.
5. **Same conda env** (`pypsa-eur`) runs both TIMES extractors.

---

## 9. Overlap matrix (all three repos)

| Area | TIMES_PyPSA | pypsa-wal | ClimAct | Overlap |
|------|-------------|-----------|---------|---------|
| `.vd` parsing | ✓ | ✓ (duplicate) | — | **Full code duplicate** |
| Demand CSVs | `pypsa_demands_*` | `wallon_demands_*` | — | Same logic, different names |
| Mapping tables (TIMES) | `data/mapping_*.csv` | `data/walloon/mapping_*.csv` | — | **Duplicated files** (synced) |
| Extraction rules | hard-coded dict | `extraction_rules.csv` | — | **Drifted** (§10) |
| Sankey / flow viz (TIMES) | Plotly HTML + link CSV | same (in fork) | — | **Duplicated** |
| Sankey / flow viz (PyPSA) | — | SEPIA (separate) | carrier×sector CSVs | **Parallel concept, no shared code** |
| Sector/component mapping | TIMES process codes | — | `sector_mapping.csv` | **Independent ontologies** |
| PyPSA solve | — | ✓ | — | Unique to pypsa-wal |
| Explorer CSVs | — | upload staging | ✓ extract | Sequential pipeline |
| Explorer TIMES tab | — | `.vd` upload | — | Raw file, no library |
| PyPSA version | 1.x (via pypsa-eur) | 1.x | 0.35.x | **Incompatible** |

---

## 10. Inconsistencies between the two demand extractors

Comparing hard-coded rules in `bau_sankey_diagram.py` (ll. 624–693) against
`pypsa-wal/data/walloon/extraction_rules.csv`:

| # | Difference | TIMES_PyPSA | pypsa-wal | Impact |
|---|-----------|-------------|-----------|--------|
| A | `retro` category | absent (53 cat.) | present (54 cat.) | pypsa-wal extracts retrofit savings row this repo never produces |
| B | `methane` biogas spelling | `biogaz épuré` (accented) | `biogaz epure` | No numeric effect today (neither exists as Agg. Level 2) — drift signal |
| C | `hydrogen road` carrier | `Hydrogen for transport` | `H2 for transport` | Both 0.0 today; will diverge when H2 road appears |
| D | Extra CSV `filter_field_2` columns | not present | present but ignored unless `filter_type == 'combined'` | Misleading documentation in CSV |

**Conclusion:** 52 of 53 shared categories match today; `retro` differs. One edit
away from wider divergence. Central maintenance risk.

---

## 11. Gaps — "is anything missing?"

### 11.1 Produced but never consumed: `heating_capacities_{h}.csv`

Snakemake declares this output but **no rule reads it**. PyPSA-Wal seeds heating
stock from PyPSA-Eur defaults (`add_existing_baseyear.py`), not TIMES
`VAR_Cap`/`VAR_Ncap`. Either wire TIMES heating stock in or stop producing the file.

### 11.2 Categories always zero

In demo and baseline: `ammonia`, `methanol`, `hydrogen road`,
`total international navigation` → 0.0 every year. Mapping gap or genuinely
absent in TIMES-WAL — confirm with modeller (Q3).

### 11.3 Non-demand quantities not soft-linked

Hand-maintained in pypsa-wal, not extracted from `.vd`:

- `custom_potentials.csv`, `custom_costs_rc.csv`
- `agg_p_nom_minmax_*.csv`, `ntc_*.csv`
- `wal_2021_existing_capacities_2.csv` (has `TIMES_proc` column but manual values)

The `.vd` contains corresponding variables (`VAR_Cap`, `Cost_*`, etc.) — could be
extracted in Phase 2 after modeller agreement (§17, §18).

### 11.4 No cross-validation between TIMES and PyPSA energy-flow views

TIMES Sankey link tables and ClimAct `supply_energy_df.csv` represent the same
physical system at different pipeline stages, but:

- No script compares them
- Mapping ontologies are independent
- Units/aggregation differ (PJ links vs TWh pivots)

A future QA step could compare TIMES-extracted demands against ClimAct-reported
loads for BEWAL — not implemented.

### 11.5 Stale artefacts

- Old `pypsa_demands_*.csv` with `MW` column no longer produced
- `pypsa-wal` adds `year` column to `wallon_demands_*.csv`; TIMES_PyPSA puts year in filename only
- `config.walloon.yaml` references `data/agg_p_nom_minmax.csv` which is absent (only `data/walloon/agg_p_nom_minmax_*.csv` exist)

---

## 12. Explorer publishing pipeline

End-to-end (from `pypsa-wal/instructions.md` + `times_data_extraction.md`):

```
1. snakemake solve  →  results/walloon-model/networks/*.nc
2. upload raw       →  s3://…/pypsa_raw_results/YYYYMMDD_walloon-model/
3. ClimAct extract  →  49 pypsa/*.csv + strategy/*.csv
4. stage + upload   →  s3://…/scenarios/pypsa__walloon-model__YYYYMMDD/
       ├── pypsa/       ← ClimAct output
       ├── strategy/    ← ClimAct strategy metrics
       └── times/       ← raw .vd (cp -L, no parsing)
5. Explorer test site → select scenario, clear cache
```

Scenario folder naming: `<type>__<scenario>__YYYYMMDD` (three parts required).

Operational detail: `pypsa-wal/instructions.md`, `times_data_extraction.md`,
`cluster/upload_s3.sh`. S3 bucket `intervectoriel`, profile `intervectoriel`.

---

# Part II — Unification strategy

## 13. Target workflow

The user provides one **coupling run folder**; a single library export feeds
PyPSA; publishing chains ClimAct extraction and S3 upload.

```
┌────────────────────────────────────────────────────────────────────────────┐
│  USER provides <coupling_dir>/:                                            │
│    • times/scenario.vd                                                      │
│    • params.yaml  (non-TIMES fixed parameters, §17)                         │
└───────────────┬────────────────────────────────────────────────────────────┘
                │  (1) times_pypsa export
                ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  times_pypsa library → <coupling_dir>/pypsa_inputs/  (manifest in §16)       │
└───────────────┬────────────────────────────────────────────────────────────┘
                │  (2) coupling_dir: in config.walloon.yaml
                ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  snakemake --configfile config/config.walloon.yaml … -call                  │
│  reads pre-exported CSVs (no inline .vd parsing in pypsa-wal)               │
└───────────────┬────────────────────────────────────────────────────────────┘
                │  (3) publish
                ▼
┌────────────────────────────────────────────────────────────────────────────┐
│  ClimAct extraction → S3 scenarios/…/pypsa|strategy|times/ → Explorer        │
│  Fallback: SEPIA HTML + scripts/rsync_output.sh → labothap                  │
└────────────────────────────────────────────────────────────────────────────┘
```

**Core design choice (D1):** the `times_pypsa` library is the **single**
extractor. PyPSA-Wal **consumes** exported `wallon_demands_{h}.csv`; delete
`pypsa-wal/scripts/build_wallon_demands.py`. Keeping extraction inside Snakemake
perpetuates the duplicated fork documented in §10.

---

## 14. Design decisions (blocking)

| ID | Decision | Status / choice | Why |
|----|----------|-----------------|-----|
| **D1** | Who owns demand extraction? | **CONFIRMED; partially implemented (2026-07-18):** logic in `times_pypsa` package; pypsa-wal keeps a thin Snakemake wrapper only. | Eliminates drift (§10). Full `<coupling_dir>` pre-export still pending (§16). |
| **D2** | Where do mapping tables live? | **In progress:** canonical copies in `times_pypsa/mappings/`; pypsa-wal still overrides via `sector.times_mappings_dir: data/walloon`. | Remove pypsa-wal copies once configs point at the package. |
| **D3** | Which non-demand quantities are soft-linked? | Phase 1: **demands only**. Phase 2: capacities / potentials / costs — **only after modellers agree** per quantity (Q6). | Avoids silently overriding tuned PyPSA assumptions. |
| **D4** | Library packaging | **Implemented (v0.1.0):** pip package `times-pypsa`, CLI + Python API (§15). | Standalone Sankey + Snakemake export share one code path. |

**Do not merge ClimAct into TIMES_PyPSA** — different PyPSA versions (1.x vs
0.35.x) and lifecycle stages. Orchestrate with a wrapper script; do not unify
codebases.

**Sankey rendering:** keep Plotly Sankey in `times_pypsa` (QA / labothap).
ClimAct continues producing Explorer pivot CSVs from `.nc`. Optional Phase 4:
validation script comparing TIMES demands vs ClimAct BEWAL loads (§6, §11.4).

---

## 15. `times_pypsa` package — layout, install, CLI, API

The library is a **pip-installable package** (`times-pypsa` v0.1.0) in this
repository. Extraction rules come from **`extraction_rules.csv`** (not the old
hard-coded dict). Core logic lives in `times_pypsa/pipeline.py` (~1 200 lines,
no Snakemake dependency).

### 15.1 Package layout

```
TIMES_PyPSA/
├── pyproject.toml              # entry point: times-pypsa = times_pypsa.cli:main
├── times_pypsa/
│   ├── __init__.py             # public API re-exports
│   ├── pipeline.py             # parse .vd, extract demands, Sankey, heating stock
│   ├── cli.py                  # argparse subcommands export | sankey
│   └── mappings/               # bundled defaults (package data)
│       ├── mapping_commodities.csv
│       ├── mapping_processes.csv
│       └── extraction_rules.csv
├── scripts/
│   └── bau_sankey_diagram.py   # thin standalone wrapper (repo-root usage)
└── data/                       # example .vd files (not installed as package data)
```

**Dependencies:** `pandas`, `plotly` only — runs in the same `pypsa-eur` conda
env as pypsa-wal (no PyPSA import required for extraction).

### 15.2 Install

```bash
cd /path/to/TIMES_PyPSA
pip install -e .

# pypsa-wal conda env (sibling checkout ../TIMES_PyPSA):
conda env update -n pypsa-eur -f envs/environment.yaml --prune
```

### 15.3 Standalone usage (Sankey QA, labothap sync)

```bash
times-pypsa export --vd data/scen_base_coherence_3110.vd --out output/ \
  --horizons 2025 2030 2040 2050 --emit all

times-pypsa sankey --vd data/scen_base_coherence_3110.vd --year 2030 --out-dir output/
```

Writes: `wallon_demands_{h}.csv`, `heating_capacities_{h}.csv`,
`bau_sankey_{h}_pj_clustered.html`, `annual_flows_{h}_energy_clustered.csv`,
`annual_values_clustered.csv`.

Backward-compatible: `python scripts/bau_sankey_diagram.py` from repo root.

**Python API:**

```python
from times_pypsa import export_horizon, default_mappings_dir

export_horizon(
    vd_file="scenario.vd",
    mappings_dir=default_mappings_dir(),
    horizon=2030,
    wallon_demands_path="wallon_demands_2030.csv",
    heating_capacities_path="heating_capacities_2030.csv",
    emit_sankey=True,
    sankey_dir="output/sankey",
)
```

### 15.4 pypsa-wal integration (Snakemake)

Rule `build_wallon_demands` delegates to `export_horizon()`. Config:

```yaml
sector:
  times_demand: true
  times_file: data/walloon/scen_base_coherence_3110.vd
  times_mappings_dir: data/walloon
```

Snakemake script is ~40 lines — **merge hygiene:** TIMES parsing stays out of
pypsa-wal; only guarded `if times_demand:` blocks in sector scripts remain
Walloon overlays when pulling pypsa-eur upstream.

### 15.5 Planned extensions

- `manifest.json` provenance on export
- Fail-fast validation (empty rule filters, multi-region `.vd`, horizon checks)
- Pre-export to `<coupling_dir>/pypsa_inputs/` without re-parsing in Snakemake (§16)

---

## 16. `<coupling_dir>` contract & PyPSA input manifest

### 16.1 Folder layout

```
<coupling_dir>/
├── times/
│   └── scenario.vd                     # USER-PROVIDED
├── params.yaml                         # USER-PROVIDED (§17)
├── mappings/                           # from library (D2)
│   ├── mapping_commodities.csv
│   ├── mapping_processes.csv
│   └── extraction_rules.csv
└── pypsa_inputs/                       # PRODUCED by times_pypsa export
    ├── wallon_demands_2025.csv
    ├── wallon_demands_2030.csv
    ├── wallon_demands_2040.csv
    ├── wallon_demands_2050.csv
    ├── heating_capacities_2025.csv
    ├── heating_capacities_2030.csv
    ├── heating_capacities_2040.csv
    ├── heating_capacities_2050.csv
    └── manifest.json
```

Introduce a single config knob `coupling_dir:` in `config.walloon.yaml` and
rebase paths below onto it. **Bold** = path hard-coded in a rule/script today
and needs a code change, not just config.

| # | File (current path) | Where set | Soft-linked? | Automation action |
|---|---------------------|-----------|--------------|-------------------|
| 1 | `data/walloon/scen_base_coherence_3110.vd` | `config.walloon.yaml` → `sector.times_file` | source | `<coupling_dir>/times/scenario.vd` |
| 2 | `resources/…/wallon_demands_{h}.csv` | rule `build_wallon_demands` | **YES** | copy from `pypsa_inputs/` (D1); delete extractor |
| 3 | `resources/…/heating_capacities_{h}.csv` | same rule | **YES (unused, §11.1)** | wire into `add_existing_baseyear` or drop (Q2) |
| 4 | **`data/walloon/mapping_processes.csv`** | `build_sector.smk` l.34 | mapping | library / `<coupling_dir>/mappings` (D2) |
| 5 | **`data/walloon/mapping_commodities.csv`** | `build_sector.smk` l.35 | mapping | idem |
| 6 | **`data/walloon/extraction_rules.csv`** | `build_sector.smk` l.36 | rules | idem |
| 7 | `data/walloon/custom_potentials.csv` | `config.walloon.yaml` → `electricity.walloon_potentials` | Phase 2 candidate | rebase; later emit from `.vd` |
| 8 | `data/walloon/custom_costs_rc.csv` | `config.walloon.yaml` → `costs.custom_cost_fn` | Phase 2 candidate | rebase; later emit from `.vd` `Cost_*` |
| 9 | `data/agg_p_nom_minmax.csv` | `solving.agg_p_nom_limits.file` (**missing**, Q7) | Phase 2 candidate | fix path; rebase |
| 10 | **`data/walloon/ntc_{h}.csv`** | `build_sector.smk` l.1814 | assumption | rebase; keep manual unless TIMES has NTCs |
| 11 | **`data/walloon/wal_2021_existing_capacities_2.csv`** | `build_electricity.smk` l.64 | partly (`TIMES_proc`) | rebase; Phase 2 reconcile with `.vd` |
| 12 | **`data/walloon/be.json`** | hard-coded in `.smk` | no (geometry) | keep static in repo |
| 13 | **`data/walloon/households.csv`** | `postprocess.smk` l.300 | no (postproc) | keep static in repo |

The mixture of config-driven (#1, 7–9) and rule-hard-coded (#4–6, 10–13)
paths prevents rebasing via `coupling_dir` alone — **promote hard-coded paths
to config** in Phase 1 (risk R4).

---

## 17. Non-TIMES parameters (`params.yaml`)

Fixed inputs that do **not** come from the `.vd` and are not standard PyPSA-Eur
defaults. They must stay **consistent with the TIMES scenario** (e.g. if TIMES
assumes 1 340 MW Walloon nuclear in 2050, PyPSA capacity bounds must agree).
Document them in `<coupling_dir>/params.yaml` so a run is fully described by
*(`.vd`)* + *(params.yaml)* + *(library version)*.

### 17.1 Config-level (`config/config.walloon.yaml`)

| Parameter | Current value | Must match TIMES? |
|-----------|---------------|-------------------|
| `scenario.planning_horizons` | `[2025, 2030, 2040, 2050]` | **Yes** |
| `countries` | `[BE, FR, GB, NL, DE, LU]` | geography only |
| `electricity.extendable_nuclear_links` | per-horizon; BE nodes from 2040 | **Yes** |
| `electricity.retrofit_nuclear_once` | `true` | **Yes** |
| `electricity.apply_ntc_constraints` | `True` (+ NTC files #10) | grid assumption |
| `sector.solid_biomass_import.enable` | `true` | **Yes** |
| `sector.co2_sequestration_potential` | `{2030:20 … 2050:125}` Mt | **Yes** |
| `sector.shipping_methanol_share` / `shipping_oil_share` | per horizon | **Yes** |
| `sector.district_heating.potential` | `0.3` | **Yes** |
| `co2_budget_national` | `{2025:0.64 … 2050:0.05}` | **Yes** |
| `solving.agg_p_nom_limits.*` | various `true` | technical |

### 17.2 Hard-coded in Python (must be surfaced)

| Parameter | Value | Location | Must match TIMES? |
|-----------|-------|----------|-------------------|
| BEWAL / BEVLG nuclear floor | **1 980 MW each** | `solve_network.py` | **Yes** |
| 1990 CO₂ baseline | BEWAL **31 Mt**, BEVLG **56 Mt**, BEBRU **9 Mt** | `add_co2limit_country()` | **Yes** |

**Proposal:** generate PyPSA config overlay from `params.yaml`; promote §17.2
values to config keys read from it.

### 17.3 CSV-level assumptions (files #7–11 in §16)

- `custom_potentials.csv`: onwind 6 500 MW, solar 13 000 MW, rooftop 46 000 MW,
  biomass import 4 000–6 000 GWh/a, biogas 8 300 GWh/a, CCGT floor 1 740 MW…
- `custom_costs_rc.csv`: fuel prices, nuclear CAPEX 9 500 €/kWₑ, onwind 1 450 €/kWₑ…
- `agg_p_nom_minmax_*.csv`: BE/BEWAL capacity min/max incl. nuclear 1 340 MW 2050
- `ntc_{h}.csv`: cross-border NTCs (MW)
- `wal_2021_existing_capacities_2.csv`: base-year inventory with `TIMES_proc` link

Phase 2: emit these from `.vd` where modellers agree TIMES is authoritative (Q6).

---

## 18. Phased implementation plan

Safe to build **Phase 0 + Phase 1** once D1/D2 are confirmed. Phase 2 waits on
Q6. Phase 3 operational path exists via `pypsa-wal/cluster/upload_s3.sh`; keep
SEPIA/labothap as fallback.

### Phase 0 — de-duplicate (prerequisite, low risk)

| ID | Task |
|----|------|
| **P0.1** | ~~Parametrise extractor; add `times_pypsa` package + CLI~~ **Done (2026-07-18)** | |
| **P0.2** | ~~Loop `heating_capacities` over all horizons~~ **Done** in `export_all_horizons` | |
| P0.3 | Single rule source: `extraction_rules.csv` in both repos; fix `retro`, biogaz, H2 road spellings (§10) |
| P0.4 | ~~Archive `extract_pypsa_demands.py`~~ **Done** (removed; noted in README) |

### Phase 1 — wire demands through `<coupling_dir>` (medium risk)

| ID | Task |
|----|------|
| P1.1 | Add `coupling_dir:` to `config.walloon.yaml` |
| **P1.2** | ~~Replace inline parser~~ **Done:** thin Snakemake wrapper calls `export_horizon`. Next: optional copy-from-`<coupling_dir>/pypsa_inputs/` without re-parsing `.vd`. | |
| P1.3 | Promote hard-coded rule paths (#4–6, #10–13, §16) to config keys under `coupling_dir` |
| P1.4 | Wrapper `run_coupled.sh <coupling_dir>` → `times_pypsa export` → `snakemake … -call` |

### Phase 2 — soft-link capacities / costs / potentials (higher risk, needs Q6)

- Emit `custom_potentials.csv`, `custom_costs_rc.csv`, `agg_p_nom_minmax.csv`;
  reconcile `wal_2021_existing_capacities` from `.vd` (`PAR_CapUP`, `VAR_Cap`,
  `Cost_*`).
- Wire `heating_capacities` into `add_existing_baseyear` (Q2).

### Phase 3 — publish to Explorer (orchestration)

- After solve: ClimAct extraction (`datapypsa` env) → stage
  `results/…/explorer/{pypsa,strategy,times}/` → `./cluster/nic5.sh upload`
  (documented in `pypsa-wal/instructions.md`).
- Include `manifest.json` + `run.json` in S3 upload for provenance (R11).
- **Fallback:** SEPIA post-processing + `scripts/rsync_output.sh` → labothap.
- **Cluster note (R13):** if inputs live outside the repo, materialise
  `pypsa_inputs/` into `resources/` before `nic5.sh push`, or extend rsync.

### Phase 4 — flow QA (optional)

- Compare TIMES demand categories vs ClimAct BEWAL loads; optional crosswalk
  between `mapping_processes.csv` Aggregation Level 2 and `sector_mapping.csv`
  sector names (Q8).

---

## 19. Risks and mitigations

| ID | Risk | Severity | Mitigation |
|----|------|----------|------------|
| **R1** | Extractor drift (§10) | High | D1/D2, Phase 0, fail-fast rule validation |
| **R2** | Silent no-op filters → 0 demand | High | Export-time empty-match warnings; treat unexpected zeros as errors (Q3) |
| **R3** | TIMES code renames break mappings | High | Version mappings with library; CI map every `.vd` code |
| **R4** | Hard-coded `.smk`/`.py` paths | Medium | P1.3 — promote to config |
| **R5** | `heating_capacities` unused (§11.1) | Medium | Q2: wire in or drop |
| **R6** | Missing `agg_p_nom_minmax.csv` (Q7) | Medium | Fix path; validate inputs before Snakemake |
| **R7** | Non-TIMES params inconsistent with TIMES (§17) | High | `params.yaml` + reconciliation checklist; Phase 2 derivation |
| **R8** | Multi-region `.vd` double-counts | Medium | Region check in export (Q5) |
| **R9** | Horizon mismatch | Low | Export validates horizons exist |
| **R10** | Road/rail netting quirk breaks silently | Low | Unit test on fixed `.vd` with known TWh per category |
| **R11** | No provenance on published results | Medium | `manifest.json` in S3 upload |
| **R12** | Explorer / ClimAct pypsa version split | Medium | Keep separate envs; orchestrate, don't merge code |
| **R13** | NIC5 push omits external `coupling_dir` | Medium | Materialise into `resources/` before push |

---

## 20. Open questions for specialists

- **Q1 — Authoritative extractor.** D1 confirms: library wins; delete pypsa-wal fork.
- **Q2 — Heating stock (§11.1).** Wire TIMES `heating_capacities` into PyPSA existing stock?
- **Q3 — Zero categories (§11.2).** Mapping gap or genuinely absent in TIMES-WAL?
- **Q4 — Road/rail netting.** Intended in extractor *and* `build_transport_demand.py`?
- **Q5 — Multi-region `.vd`.** Add `--region RW` filter?   No: the vd fild for wallonia is always single region.
- **Q6 — Non-demand soft-linking (§11.3, §17).** TIMES or PyPSA authoritative per quantity?   
- **Q7 — `agg_p_nom_minmax.csv` path.** Which file should config point to?
- **Q8 — TIMES vs PyPSA flow charts (§6, §11.4).** Harmonised sector labels for Explorer? Who maintains the crosswalk?

**Phase gates:** Phase 0+1 after D1/D2 confirmed. Phase 2 after Q6. Phase 3
can proceed independently (S3 workflow operational for test env). No guesses on
physical meaning of zero categories (Q3) or road/rail netting (Q4) — confirm
with TIMES author.
