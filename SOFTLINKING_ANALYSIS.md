# TIMES → PyPSA soft-linking

**Status (2026-07-18):** Phase 0 + Phase 1 implemented and verified. Demand and heating-capacity CSVs match the pre-refactor July 17 baseline exactly (`max |ΔTWh| = 0` for all horizons).

**Repos:**
- `TIMES_PyPSA` — `times_pypsa` library (extraction + Sankey QA + coupling export)
- `pypsa-wal` — consumes exports via Snakemake (`build_wallon_demands`)
- ClimAct extraction — post-solve Explorer CSVs only (separate env, not part of soft-linking)

---

## 1. Architecture

```
TIMES .vd
    │
    ├─ times-pypsa export / export-coupling     (library)
    │       └─ wallon_demands_{h}.csv
    │          heating_capacities_{h}.csv
    │          (+ Sankey HTML / flow tables for QA)
    │
    └─ pypsa-wal rule build_wallon_demands
            │  either re-parse .vd via export_horizon()
            │  or copy from <coupling_dir>/pypsa_inputs/
            ▼
        resources/<run>/wallon_demands_{h}.csv
            ▼
        sector build → prepare_sector_network → solve → .nc
            ▼
        ClimAct extract → Explorer (pypsa/ + strategy/)
        raw .vd upload  → Explorer (times/)
```

**Design choices in force:**
| ID | Choice |
|----|--------|
| D1 | Single extractor: `times_pypsa` package. pypsa-wal keeps a thin Snakemake wrapper only. |
| D2 | Canonical mappings in `times_pypsa/mappings/`; pypsa-wal may override with `sector.times_mappings_dir` (currently `data/walloon`, byte-identical). |
| D3 | Phase 1 soft-links **demands** (+ unused heating capacities). Potentials/costs/NTCs stay manual until modellers agree (Phase 2). |
| D4 | ClimAct stays separate (pypsa 0.35.x vs pypsa-wal 1.x). |

---

## 2. Package layout & install

```
TIMES_PyPSA/
├── pyproject.toml                 # times-pypsa CLI entry point
├── times_pypsa/
│   ├── pipeline.py                # parse .vd, extract, Sankey, coupling export
│   ├── cli.py                     # export | export-coupling | sankey
│   └── mappings/                  # bundled defaults (package data)
│       ├── mapping_commodities.csv
│       ├── mapping_processes.csv
│       └── extraction_rules.csv   # 54 categories (incl. retro)
├── scripts/
│   ├── bau_sankey_diagram.py      # thin legacy wrapper
│   └── run_coupled.sh             # export (+ optional snakemake)
└── data/                          # example .vd files
```

```bash
# In pypsa-eur conda env (sibling checkout expected):
cd /path/to/TIMES_PyPSA && pip install -e .
# or via pypsa-wal:
conda env update -n pypsa-eur -f envs/environment.yaml --prune
#   → pip: -e ../TIMES_PyPSA
```

Dependencies: `pandas`, `plotly` only (no PyPSA import for extraction).

---

## 3. How to run

### 3.1 Standalone export / Sankey QA

```bash
cd /path/to/TIMES_PyPSA

times-pypsa export \
  --vd data/scen_base_coherence_3110.vd \
  --out output/ \
  --horizons 2025,2030,2040,2050 \
  --emit all          # demands | sankey | all

times-pypsa sankey \
  --vd data/scen_base_coherence_3110.vd \
  --year 2030 \
  --out-dir output/
```

Writes `wallon_demands_{h}.csv` (canonical) and `pypsa_demands_{h}.csv` (alias), plus `heating_capacities_{h}.csv`, Sankey HTML, and flow tables.

### 3.2 Coupling bundle (`<coupling_dir>`)

```
<coupling_dir>/
├── times/scenario.vd              # user-provided (or single times/*.vd)
├── mappings/                      # copied from library if missing
└── pypsa_inputs/                  # produced by export-coupling
    ├── wallon_demands_{h}.csv
    ├── pypsa_demands_{h}.csv      # alias
    ├── heating_capacities_{h}.csv
    └── manifest.json              # version, vd, horizons, timestamp
```

```bash
# Option A — CLI
times-pypsa export-coupling \
  --coupling-dir /path/to/coupling_run \
  --vd /path/to/scenario.vd \
  --horizons 2025,2030,2040,2050

# Option B — helper (expects times/*.vd already in the folder)
./scripts/run_coupled.sh /path/to/coupling_run              # export only
./scripts/run_coupled.sh /path/to/coupling_run --snakemake  # + build demands in pypsa-wal
```

### 3.3 Inside pypsa-wal (Snakemake)

```yaml
# config/config.walloon.yaml
# coupling_dir: /path/to/coupling_run   # optional
sector:
  times_demand: true
  times_file: data/walloon/scen_base_coherence_3110.vd
  times_mappings_dir: data/walloon
  # times_use_preexported: true         # require copy from coupling_dir
```

```bash
cd /path/to/pypsa-wal

# Rebuild demands only
snakemake --configfile config/config.walloon.yaml -c4 \
  resources/walloon-model/wallon_demands_{2025,2030,2040,2050}.csv

# Use pre-exported coupling inputs (no .vd re-parse)
snakemake --configfile config/config.walloon.yaml \
  --config coupling_dir=/path/to/coupling_run \
  resources/walloon-model/wallon_demands_2030.csv
```

Rule `build_wallon_demands` (`scripts/build_wallon_demands.py`, ~50 lines):
- if `coupling_dir` is set and `pypsa_inputs/wallon_demands_{h}.csv` exists → **copy**;
- if `times_use_preexported: true` → copy is **required** (fail if missing);
- else → `times_pypsa.export_horizon(...)`.

### 3.4 Python API

```python
from times_pypsa import export_horizon, export_coupling_dir, default_mappings_dir

export_horizon(
    vd_file="scenario.vd",
    mappings_dir=default_mappings_dir(),
    horizon=2030,
    wallon_demands_path="wallon_demands_2030.csv",
    heating_capacities_path="heating_capacities_2030.csv",
)

export_coupling_dir(
    coupling_dir="/path/to/coupling_run",
    vd_file="scenario.vd",
    horizons=[2025, 2030, 2040, 2050],
)
```

---

## 4. Extraction mechanics (edit here when changing results)

1. Parse `.vd` → `[year, region, timeslice, variable, commodity_code, process_code, value]`
2. Aggregate timeslices → annual PJ
3. Map via `mapping_processes.csv` (`Aggregation Level 2` → column **`process_agg`**) and `mapping_commodities.csv` (`PyPSA Energy Carrier`)
4. Apply 54 rules from `extraction_rules.csv`
5. Net internal transfers (`net_bidirectional_links`) inside aggregated process groups
6. PJ → TWh (`× 0.277778`)
7. Post-process: subtract `electricity rail` from `electricity road`, `total rail` from `total road`

**QA / validation:** use `times-pypsa qa` (multi-level Sankey with export highlighting, balance and coverage CSVs). Full data model, aggregation levels, tests, and expert questions: **[README.md § Extraction QA](README.md#extraction-qa)**.

**Where to edit:**
| What | File |
|------|------|
| Category definitions / filters | `times_pypsa/mappings/extraction_rules.csv` (sync to `pypsa-wal/data/walloon/` if overriding) |
| Process aggregation labels | `mapping_processes.csv` |
| Commodity → PyPSA carrier | `mapping_commodities.csv` |
| Parser / netting / road-rail | `times_pypsa/pipeline.py` |
| QA report / balances / topology | `times_pypsa/{qa,balances,model,topology,aggregation}.py` |
| Snakemake I/O only | `pypsa-wal/scripts/build_wallon_demands.py` |

Keep both mapping copies in sync (or drop the pypsa-wal override and use package defaults).

---

## 5. Verification (2026-07-18)

Against July 17 `resources/walloon-model/wallon_demands_*.csv` and `heating_capacities_*.csv` (`scen_base_coherence_3110.vd`):

| Check | Result |
|-------|--------|
| 54 categories all horizons | identical |
| `max \|ΔTWh\|` demands | **0** |
| heating capacities | **byte-equal** |
| Coupling export vs baseline | **0** |
| Mapping CSVs package ↔ `data/walloon` | same md5 |
| Snakemake after `--touch` | `Nothing to be done` |

Cherry-pick 2030 (TWh): residential elec 3.987, total road 22.735, elec road 0.457, methane 11.091, BEWAL rural heat 10.775, retro 0.775, H₂ road 0.0.

Networks under `results/walloon-model/networks/` were **not re-solved**; the DAG was marked complete with `snakemake … --touch` after confirming input CSVs unchanged.

---

## 6. Downstream publishing (not soft-linking, but coupled runs)

1. Solve (or reuse touched results) → `results/walloon-model/networks/*.nc`
2. ClimAct extract in `datapypsa` env → 49 `pypsa/*.csv` + `strategy/`
3. Stage `explorer/{pypsa,strategy,times/}` (TIMES = raw `.vd`)
4. `./cluster/nic5.sh upload` → S3 `intervectoriel` (see `pypsa-wal/instructions.md`, `times_data_extraction.md`)

SEPIA HTML + `scripts/rsync_output.sh` → labothap remains a fallback for Sankey-style QA.

---

## 7. Implementation status

| Phase | Item | Status |
|-------|------|--------|
| **0** | Package + CLI + thin Snakemake wrapper | **Done** |
| **0** | Shared `extraction_rules.csv` (54 cats, incl. `retro`) | **Done** |
| **0** | Heating capacities for all horizons | **Done** (produced, unused) |
| **1** | `export-coupling` + `manifest.json` + `run_coupled.sh` | **Done** |
| **1** | Optional `coupling_dir` copy path in pypsa-wal | **Done** |
| **1** | Promote remaining hard-coded `.smk` paths (#10–13 NTC, capacities, …) | Open |
| **2** | Soft-link potentials / costs / NTCs / existing stock from `.vd` | Open (needs Q6) |
| **2** | Wire `heating_capacities` into `add_existing_baseyear` | Open (needs Q2) |
| **3** | Explorer upload orchestration | Operational via `nic5.sh` |
| **4** | TIMES demands vs ClimAct BEWAL load QA script | Open |
| **4** | Multi-view extraction QA (`times-pypsa qa`) + pytest balances | **Done** (see [README.md § Extraction QA](README.md#extraction-qa)) |

---

## 8. Unresolved questions & attention points

### Open questions (need modeller input)

- **Q2 — Heating stock.** `heating_capacities_{h}.csv` is written by Snakemake but never read. Wire TIMES `VAR_Cap`/`VAR_Ncap` into PyPSA existing heat stock, or stop producing the file?
- **Q3 — Persistent zeros.** `ammonia`, `methanol`, `total international navigation` are 0 every year; `hydrogen road` is 0 until 2050 (then ~3.92 TWh). Mapping gaps or genuinely absent in TIMES-WAL?
- **Q4 — Road/rail netting.** Applied in the extractor **and** again in `build_transport_demand.py` when `times_demand: true`. Intended double-handling or leftover?
- **Q6 — Non-demand soft-linking.** For each of `custom_potentials`, `custom_costs_rc`, `agg_p_nom_minmax_*`, `ntc_*`, `wal_2021_existing_capacities`: is TIMES or the hand-tuned PyPSA CSV authoritative?
- **Q7 — `agg_p_nom_minmax.csv` path.** Config historically pointed at a missing `data/agg_p_nom_minmax.csv`; only `data/walloon/agg_p_nom_minmax_*.csv` exist. Which file should the walloon config use?
- **Q8 — Flow-chart crosswalk.** TIMES Sankey (`mapping_processes` Aggregation Level 2) vs ClimAct Explorer (`sector_mapping.csv`) are independent ontologies. Worth a maintained crosswalk for QA?

**Resolved:** Q1 (library owns extraction); Q5 (Wallonia `.vd` is always single-region — no `--region` filter needed).

### Attention / risks

| Topic | Note |
|-------|------|
| Mapping drift | Two copies (`times_pypsa/mappings/` and `data/walloon/`). Prefer pointing config at the package or syncing in CI. |
| Silent empty filters | A typo in `extraction_rules.csv` yields 0 demand with no hard failure. Mitigated by `times-pypsa qa` empty-rule table + pytest allowlist (`KNOWN_ZERO_CATEGORIES`). |
| Params vs TIMES | Nuclear floors, CO₂ baselines, shipping shares, sequestration potentials live in `config.walloon.yaml` / Python (`solve_network.py`) — must stay consistent with the TIMES scenario (future `params.yaml`). |
| NIC5 + external `coupling_dir` | Materialise `pypsa_inputs/` into `resources/` before `nic5.sh push`, or extend rsync. |
| PyPSA version split | Never merge ClimAct into this package; orchestrate envs only. |
| Config param churn | Adding keys under `sector:` can mark many rules stale (params hash). After demand-only changes with proven identical CSVs, `snakemake … --touch` is appropriate. |

---

## 9. Quick reference — key paths

| Role | Path |
|------|------|
| Library core | `TIMES_PyPSA/times_pypsa/pipeline.py` |
| Extraction QA | `TIMES_PyPSA/README.md` (§ Extraction QA), `times-pypsa qa` |
| CLI | `times-pypsa` → `times_pypsa/cli.py` |
| Bundled mappings | `TIMES_PyPSA/times_pypsa/mappings/` |
| Snakemake wrapper | `pypsa-wal/scripts/build_wallon_demands.py` |
| Rule | `pypsa-wal/rules/build_sector.smk` → `build_wallon_demands` |
| Walloon config | `pypsa-wal/config/config.walloon.yaml` |
| Demand outputs | `pypsa-wal/resources/walloon-model/wallon_demands_{h}.csv` |
| Baseline `.vd` | `data/walloon/scen_base_coherence_3110.vd` (same file in both repos) |
| Explorer publish docs | `pypsa-wal/instructions.md`, `times_data_extraction.md` |
