# TIMES Extraction QA

Documentation for the TIMES → PyPSA soft-link extraction quality-assurance
toolkit: data model, aggregation levels, multi-view Sankey report, and tests.

**Reference scenario:** `pypsa-wal/TIMES_data/scen_corrige_251129_0112.{vd,vdt}`

Related: [SOFTLINKING_ANALYSIS.md](SOFTLINKING_ANALYSIS.md).

---

## 0. Direct answers (status)

| Question | Answer |
|----------|--------|
| Are current extraction rules adequate? | **Partially.** Parent–child heat identities match; no disallowed double-counting after the rail allowlist; topology is clean. But DMD coverage gaps remain large (~330 PJ in 2050), so we cannot claim “no forgotten demand” without TIMES-expert confirmation (see §5). |
| Former aggregation rules still active? | **Yes.** Extraction still uses `mapping_processes.csv` **Aggregation Level 2** (column `process_agg`), `mapping_commodities.csv` **PyPSA Energy Carrier**, and `extraction_rules.csv` unchanged in formalism. |
| New aggregation rules / new CSV formalism? | **No new CSV.** Sankey QA uses the same mapping fields. A code-only view level `mapping` = Level 2 × PyPSA carrier. Invented renames (e.g. AGR→“Agriculture”, “Unmapped: …”) were removed. |
| What changed in the Sankey? | Replaced unreadable full-system L0/L1 diagrams with an **export neighbourhood**: matched (exported) flows **plus n−1 and n+1** context sharing those processes/commodities. |

---

## 1. TIMES data model

### 1.1 `.vd` — results (GDX2VEDA)

Sparse table with dimensions:

`Attribute, Commodity, Process, Period, Region, Vintage, TimeSlice, UserConstraint, PV`

| Attribute | Role for soft-linking |
|-----------|------------------------|
| `VAR_FIn` | Commodity **input** to a process (fuel/feedstock), PJ |
| `VAR_FOut` | Commodity **output** from a process, PJ |
| `VAR_Comnet` | Net commodity production (region balance oracle) |
| `VAR_Cap` / `VAR_Ncap` | Capacities (heating stock export) |
| `VAR_Act` | Process activity (no commodity dimension) |

Energy Sankeys and demand extraction use **only** `VAR_FIn` / `VAR_FOut`,
annualized by summing timeslices (and effectively summing vintages via the
groupby that drops vintage).

**Link convention (Sankey):**

- `VAR_FIn`: source = commodity → target = process
- `VAR_FOut`: source = process → target = commodity

### 1.2 `.vdt` — topology

Static wiring: `Region, Process, Commodity, Direction` with `IN` or `OUT`.
Does not contain quantities. Used to flag result flows that are absent from
the declared topology (`times_pypsa.topology.load_topology`).

### 1.3 Package representation

| Module | Type |
|--------|------|
| `times_pypsa.topology.Topology` | `.vdt` links |
| `times_pypsa.model.TimesAnnualFlows` | Enriched annual flows + `VAR_Comnet` + optional topology mismatches |
| `times_pypsa.aggregation` | L0 / L1 / L2 collapse |
| `times_pypsa.qa` | Multi-view HTML report |
| `times_pypsa.balances` | Balance / loop / double-count helpers |

```bash
from times_pypsa import load_times_annual_flows, load_topology

model = load_times_annual_flows(
    "scen.vd",
    mappings_dir="times_pypsa/mappings",
    vdt_file="scen.vdt",
)
```

Enriched flow columns include: `sector`, `agg_level_1`, `agg_level_2`,
`process_agg`, `pypsa_carrier`, `commodity_sector`.

### 1.4 `process_agg` naming (important)

Extraction rules filter on **Aggregation Level 2** labels from
`mapping_processes.csv`. Historically those labels were stored in a column
named `agg_level_1`, which was confusing because Aggregation Level 1 also
exists.

**Canonical column:** `process_agg` (= Aggregation Level 2).  
`agg_level_1` is kept as a **legacy alias** of `process_agg` inside the
extractor so existing rule CSVs keep working. True Aggregation Level 1 is
available on `TimesAnnualFlows.flows["agg_level_1"]` only when loaded via
`load_times_annual_flows` (model path); the demand extractor still mirrors
Level 2 into both `process_agg` and `agg_level_1`.

---

## 2. Aggregation levels (Sankey / QA only)

Extraction itself is unchanged (filters on Aggregation Level 2 labels).

| Level | Process nodes | Commodity nodes | Use |
|-------|---------------|-----------------|-----|
| **`mapping` (default QA)** | `process_agg` = Aggregation Level 2 from CSV | `pypsa_carrier` from CSV | Export neighbourhood Sankey |
| L0 | Sector **codes** (AGR, COM, …) | Commodity sector / carrier | Optional coarse check (not primary UI) |
| L1 | Aggregation Level 1 | PyPSA carrier | Optional |
| L2 | `process_code` | `commodity_code` | Fine drill-down |

**No renaming** beyond those CSV fields (fallback = existing TIMES description or code).

### Export neighbourhood (n−1 / n / n+1)

1. Core = process and commodity codes on flows matched by extraction rules  
2. Keep every energy flow that shares a core process **or** core commodity  
3. That includes upstream producers / other inputs (n−1) and downstream consumers / other outputs (n+1)  
4. Aggregate with `mapping` labels; colour blue = exported, grey = context  

---

## 3. QA report (`times-pypsa qa`)

```bash
times-pypsa qa \
  --vd /path/to/scen_corrige_251129_0112.vd \
  --vdt /path/to/scen_corrige_251129_0112.vdt \
  --year 2050 \
  --out-dir output/qa_2050/
```

### Views

| View | Content |
|------|---------|
| **A** | Export neighbourhood Sankey (Level 2 × PyPSA carrier; blue=exported, grey=n−1/n+1) |
| **B** | Per-category neighbourhood (same rule, matched + n−1 + n+1) |
| **C** | Tables: coverage, empty rules, parent–child, double-count, Comnet, loops, DMD gaps |

### Companion CSVs

- `qa_flows_{year}.csv` — tagged annual energy flows
- `qa_export_neighborhood_{year}.csv` — matched + n−1 + n+1 rows before Sankey aggregation
- `qa_export_coverage_{year}.csv` — PJ / TWh / key counts per category
- `qa_node_balance_{year}.csv` — ΣFOut−ΣFIn vs `VAR_Comnet`
- `qa_commodity_residuals_{year}.csv` — post-netting commodity residuals
- `qa_loops_{year}.csv` — SCCs with >1 node after netting
- `qa_double_count_{year}.csv` — disallowed category overlaps
- `qa_parent_child_{year}.csv` — heat / agriculture identities
- `qa_empty_rules_{year}.csv` — zero categories vs known-zero allowlist
- `qa_coverage_gap_{year}.csv` — DMD demand-sector `VAR_FIn` not matched by any rule
- `qa_coverage_gap_all_{year}.csv` — same without DMD filter
- `qa_topology_mismatches_{year}.csv` — if `.vdt` provided and mismatches remain

---

## 4. Tests

```bash
cd TIMES_PyPSA
pip install -e ".[dev]"
pytest tests/ -q
```

Fixtures resolve `pypsa-wal/TIMES_data/scen_corrige_251129_0112.vd` (sibling
checkout) or `TIMES_PyPSA/data/` if present.

| Test module | Checks |
|-------------|--------|
| `test_topology.py` | `.vdt` parse, IN/OUT counts |
| `test_balances.py` | Comnet residuals, loops API, node residuals |
| `test_extraction_coverage.py` | empty rules allowlist, double-count allowlist, parent–child, tagging |

Known-zero allowlist (SOFTLINKING Q3): `ammonia`, `methanol`,
`total international navigation`, `coal`.

---

## 5. Questions for TIMES experts

Filled from QA run on `scen_corrige_251129_0112`, year **2050**
(`output/qa_2050/`). Re-run `times-pypsa qa` after mapping changes.

1. **Comnet residuals on energy commodities.** After restricting to
   commodities with FIn/FOut activity, some PJ carriers still disagree with
   `VAR_Comnet`. Are trade / stock / IMPEXP terms expected outside process
   flows? See `qa_node_balance_2050.csv` failures.

2. **DMD coverage gaps.** Unexported end-use (`Type=DMD`) FIN flows — see
   `qa_coverage_gap_2050.csv`. Notable examples in this scenario:
   - `international aviation` × Kerosenes (~31 PJ FIN) while the soft-link
     rule uses `VAR_FOUT` — confirm FIN vs FOUT choice.
   - `Cars` × `BATELCOUT` (~23 PJ) — battery discharge into EVs; grid
     electricity is already taken via `Fuel Tech - Electricity (TRA)` +
     `TRA_STG`. Confirm this should stay excluded (avoid double count).
   - `Buildings: built area` × Heat — large RSD heat-like flows not in
     residential boiler / BEWAL heat rules. Soft-link intentionally, or
     missing?

3. **Persistent zeros.** Confirm these stay absent (allowlisted):
   `ammonia`, `methanol`, `total international navigation`, `coal`
   (no industry hard-coal/lignite FIN in soft-link years for this scenario).
   Many residential coal/biomass/solar/oil boiler categories are also 0 in
   2050 but non-zero in earlier years — OK.

4. **Topology mismatches.** After ignoring `process_code='-'` (GHG
   aggregates), **this scenario has zero mismatches**. Re-check if a new
   `.vd`/`.vdt` pair starts reporting rows in `qa_topology_mismatches_*.csv`.

5. **Surviving loops after netting** (`qa_loops_2050.csv`):
   - Industry heat/steam cycle (`INDHET` / `INDHTH` / …)
   - Steel scrap/electric furnace cluster
   - Large bio/CHP/electricity cluster (`ELCHIG`, `INDELC`, …)
   Are these physical recycles / CHP feedbacks, or aggregation artifacts?

6. **`process_agg` label typos.** Any Aggregation Level 2 labels in
   `extraction_rules.csv` that do not exist in `mapping_processes.csv`?
   (Automated check recommended as follow-up.)

7. **Services district heating vs BEWAL services heat.** In 2050,
   BEWAL services urban decentral heat is only ~0.25 PJ (mostly gas boiler).
   Is commercial heat demand that low in this scenario, or are district /
   other techs mis-mapped?

---

## 6. Rule-change log

| Date | Change | Evidence | Demand CSV impact |
|------|--------|----------|-------------------|
| 2026-07-18 | Introduced QA toolkit; canonical filter column `process_agg` (legacy `agg_level_1` alias). Allowlist: `total rail`⊃`electricity rail`; known-zero `coal`. No `extraction_rules.csv` edits. | `output/qa_2050/` | None |
| 2026-07-18 | Sankey redesign: drop invented L0 renames; primary view = export neighbourhood (mapping CSV labels + n−1/n+1). | readability feedback | None |
