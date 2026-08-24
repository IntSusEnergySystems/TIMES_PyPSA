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

One page per model year **and** per aggregation level, for a report / results
folder (this is what pypsa-wal's `build_times_sankey` rule calls):

```bash
times-pypsa sankey-pages \
  --vd data/scen_corrige_251129_0112.vd \
  --out-dir output/report_pages/ \
  --years 2025,2030,2040,2050 \
  --agg-levels custom "Aggregation Level 2"
```

Writes `times_sankey_<level>_<year>.html` (`Aggregation Level 2` becomes the
documented alias `mapping` in the file name) plus a `times_sankey_index.html`
linking them. Each page is one interactive Sankey with the netting toggle and
cross-links to the other year and level — no QA tables, no per-year CSVs, and
**one** `.vd` parse for every page. `--years` defaults to every year in the file;
a requested year the `.vd` does not cover still gets a page saying so, so a
caller that declared the file (a Snakemake rule) sees the gap instead of failing
on a missing output. See [Sankey report pages](#sankey-report-pages).

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

### Heating soft-link (option C)

Besides the annual demands, the export carries the **technology axis** of the
Walloon heating system, so pypsa-wal can impose the TIMES appliance mix instead of
re-optimising it. Two artefacts, both driven by
[`data/heat_softlink_groups.csv`](data/heat_softlink_groups.csv):

| File | Contents |
|---|---|
| `heating_targets_{year}.csv` | annual heat output per **constraint group** (heat pump, gas / oil / biomass boiler, resistive heater, solar thermal), with the PyPSA carriers to constrain, the constraint sense, the share of decentral heat, and the TIMES child categories each group was summed from |
| `heating_capacities_{year}.csv` | installed capacity in **MW thermal output**, keyed so it drops straight into PyPSA-Eur's `existing_heating_distribution` (same unit) |

Groups are summed over `rural` + `urban decentral` + services, because the TIMES
urban/rural label is a dwelling-archetype convention rather than a TIMES result;
summing over both cancels it. District heating is exported with `sense: none` —
reported for accounting, never constrained (see
`pypsa-wal/docs/heat-softlink.md` for why).

`heat_softlink_groups.csv` is data on purpose: every arbitrary mapping (coal →
oil boiler, geothermal → heat pump, tertiary CHP heat → gas boiler) carries its
justification in a `note` column, and the loader refuses a definition that claims
a TIMES category twice, uses an unknown constraint sense, or names a
`pypsa_stock_technology` that is not an `existing_heating_distribution` column.

> `heating_capacities_{year}.csv` sums **`VAR_Cap` only**. `VAR_Ncap` (capacity
> built in the period) is already inside `VAR_Cap`; adding the two inflated the
> 2050 gas and heat-pump rows by 36–65 %. Rows are selected by explicit
> `Aggregation Level 2` label, not by regex — the old
> `boiler|heat pump|stove|thermal|heater` filter admitted `Geothermal (IND)` and a
> 1 740 MW `Thermal Public - Retrofitting CCGT CCS` power plant while dropping
> `District heating`.

### Road-transport soft-link (EV fleet)

The demands carry road **energy**; this carries the road **fleet**, because
pypsa-wal needs both and they are far apart. Its BEV-charger `p_nom` and
EV-battery `e_nom` are `number_cars × charge_rate × electric_share` — a vehicle
count — while the energy ratio `electricity road / total road` is 0.142 against a
0.529 car BEV stock share in 2030. Feeding the energy share to a count
understated the flexible fleet 3.7×. Two artefacts, driven by
[`data/transport_softlink_groups.csv`](data/transport_softlink_groups.csv):

| File | Contents |
|---|---|
| `road_transport_{year}.csv` | stock (**thousand vehicles**, `VAR_Cap`) and activity (**billion vehicle-km**, `VAR_Act`) per vehicle class × drivetrain |
| `road_transport_{year}_shares.csv` | the same collapsed onto the three PyPSA engine types, with `stock_share` and `activity_share` per class, every class carrying all three engine types zero-filled |

**pypsa-wal reads the `_shares` file** — `stock_share` (by count) for the fleet
quantities, `stock_kveh` for the vehicle count itself, and it keeps the energy
ratio for the load, which is what makes the EV grid draw equal the transferred
`electricity road` exactly. `activity_share` is the right share for a per-km
quantity and is exported for that reason; the two differ ~6 %.

> Take `stock_kveh` and `stock_share` from the **same** `vehicle_class` rows.
> Their product is the BEV count, and crossing the boundaries gets it wrong both
> ways: a car count against a cars+vans share is 14 % low at 2030, a cars+vans
> count against a car share 17 % high. Choosing *which* boundary is nearly free —
> TIMES has ~0.1 kveh of electric vans, so cars and cars+LCV give the same BEV
> count to four figures.

Three conventions the extraction depends on:

* **The selector is the unit pair `000VEH`/`BVKM`**, not the
  `Aggregation Level 2` label — `Cars` and `Road Freight` each label both the
  vehicle processes and the fuel technologies feeding them.
* **The vehicle class comes from the process description**, never the code prefix:
  fourteen `TCAR…` codes in the reference `.vd` are heavy-duty trucks and vans.
* **`VAR_Cap` only, never `VAR_Cap + VAR_Ncap`** — the same double count the
  heating capacities had to undo.

PHEV and HEV map to `ice`: pypsa-wal has no plug-in-hybrid component, and a PHEV
split across the EV-battery and oil buses would need its own utility factor. Elia
instead counts a PHEV as half a BEV — the two conventions must not be mixed. See
`pypsa-wal/docs/ev-charging-softlink.md` §2.

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
    heating_targets_path="resources/walloon/heating_targets_2030.csv",
    road_transport_path="resources/walloon/road_transport_2030.csv",
    sankey_dir="results/sankey",
    emit_sankey=True,
)
```

`heating_targets_path` and `road_transport_path` are optional in the signature;
`export_all_horizons` and `export_coupling_dir` always write both. Pass
`road_transport_path` for anything pypsa-wal consumes — its
`prepare_sector_network` needs the `_shares` file written alongside, and
`build_wallon_demands` raises on a bundle that lacks it rather than falling back
to the energy ratio.

Report pages for a results folder — parses the `.vd` once, tags each year once,
and renders every level from that:

```python
from times_pypsa import export_sankey_pages

export_sankey_pages(
    "results/walloon/scen_demande_haute/html",
    vd_file="path/to/scenario.vd",
    years=[2025, 2030, 2040, 2050],
    agg_levels=["custom", "Aggregation Level 2"],
    scenario_label="scen_demande_haute",
)
```

Pass `model=` to reuse an already-loaded `TimesAnnualFlows` instead of
re-parsing. `sankey_page_names(years, levels)` returns the exact file list
without touching the `.vd`, which is how a Snakemake rule declares its outputs
before the data exists.

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
- `heat_softlink_groups.csv`: heating constraint groups — TIMES categories and process labels ↔ PyPSA carriers and stock technologies, with the constraint sense and the justification of every arbitrary assignment
- `transport_softlink_groups.csv`: road-vehicle classes and drivetrains ↔ the three PyPSA engine types, matched on the process **description**. Declared as a Snakemake input in pypsa-wal, so editing it invalidates `road_transport_*.csv` instead of leaving a stale fleet on disk

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

- **What PyPSA receives** is each sector's **final energy demand** in PJ — the energy the end-use device consumes, not the service it delivers (Btkm, Mm²) and not the primary energy upstream. Heating is the exception: PyPSA models the heat bus, so heat rules export useful heat out of the boiler/heat pump. `measure_at` records the point each rule reads; see [aggregation.md](aggregation.md#what-pypsa-must-receive-final-energy-demand).
- **Extraction** filters on `mapping_processes.csv` **Aggregation Level 2** (column `process_agg`), `mapping_commodities.csv` **PyPSA Energy Carrier**, and the categories in `extraction_rules.csv`. Changing a Sankey/QA aggregation level never changes the exported demands.
- **Aggregation levels** are shared CSV column names selected with `--agg-level` / `aggregate_flows(level=...)`: `Aggregation Level 2` (default; alias `mapping`), `custom` (readable working level: export-touching L2 kept, context collapsed), `sankey_overview` (≤20 nodes), `Sector`. Details: [aggregation.md](aggregation.md).
- **Sankey** collapses commodity hubs to process→process flows (commodities become link labels). Process nodes are green; expected final-demand / non-PJ residuals become magenta `U ·` sink nodes; unexplained imbalances keep `Unbalanced …` labels and warn/error. Exported links are coloured by **PyPSA sector** and anchored to the demand inflow **when that conserves the highlighted energy** (see [aggregation.md](aggregation.md#export-strategy-per-sector-colours--demand-inflow-anchoring)).
- **Reconciliation** (added 2026-07-25): the report compares coloured Sankey PJ against tagged export PJ per sector, so a soft-link the diagram forgets — or colours twice — shows up as a number instead of staying invisible.
- **Adequacy**: parent–child heat identities hold, no disallowed double-count overlaps, and topology is clean for the reference scenario. ~76% of the DMD coverage gap is consumption-side (n+1/n+2) of already-exported production; ~20% is material accounting **or commodities missing from the mapping**; ~5% residual. The 2026-07-25 audit closed every defect that would change the demand CSVs, and its two remaining coupling questions were resolved the same day by reading the pypsa-wal side (see [Open points](#open-points)).

#### TIMES data model

##### `.vd` — results (GDX2VEDA)

Sparse table with dimensions:

`Attribute, Commodity, Process, Period, Region, Vintage, TimeSlice, UserConstraint, PV`

| Attribute | Role for soft-linking |
|-----------|------------------------|
| `VAR_FIn` | Commodity **input** to a process (fuel/feedstock), PJ |
| `VAR_FOut` | Commodity **output** from a process, PJ |
| `VAR_Comnet` | Net commodity production. **Only exported for the commodities GDX2VEDA was asked for** — in the reference `.vd` that is emission/pollutant aggregates only, so it is an oracle for emissions, not for energy carriers |
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
| `times_pypsa.aggregation` | Aggregation-level collapse + export colouring |
| `times_pypsa.qa` | Multi-view HTML report |
| `times_pypsa.balances` | Balance / loop / double-count helpers |

Enriched flow columns include: `sector`, `agg_level_2`, `process_agg`, `pypsa_carrier`, `commodity_sector`, `commodity_cluster`, plus `proc_agg__{level}` / `com_agg__{level}` for every shared aggregation column.

##### `process_agg` naming (important)

Extraction rules filter on **Aggregation Level 2** labels from `mapping_processes.csv`. Historically those labels were stored in a column named `agg_level_1`, which was confusing because an Aggregation Level 1 column also existed.

**Canonical column:** `process_agg` (= Aggregation Level 2). `agg_level_1` survives only as a legacy alias of `process_agg` inside the extractor so third-party rule CSVs keep working; the `Aggregation Level 1` mapping column itself was removed on 2026-07-25.

#### Aggregation levels (Sankey / QA only)

Extraction itself is unchanged (filters on Aggregation Level 2 labels). Sankey / QA aggregation is selected by a **single shared CSV column name** (process mapping × commodity mapping).

| Level (`--agg-level`) | Typical use |
|-----------------------|-------------|
| **`Aggregation Level 2`** (default; alias `mapping`) | Export neighbourhood / detailed QA |
| **`custom`** | Working level: export-touching L2 kept, context collapsed (~45 nodes) |
| `Sector` / `L2` | Coarse / fine drill-down |
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
`Fuel Tech (IND) → Industry`, not the upstream import link) — but only when the
gateway's outputs carry the same energy as its soft-linked input, so anchoring can
never invent or lose coloured PJ. Gateways that fail that check keep their
highlight upstream and are listed in `qa_anchor_ledger_{year}.csv`. The former
single blue / light-red *mixed* scheme is retired (mixed verified never to occur).
See [aggregation.md](aggregation.md#export-strategy-per-sector-colours--demand-inflow-anchoring).

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

#### Sankey report pages

`times-pypsa sankey-pages` / `export_sankey_pages` is the QA report's Sankey
without the QA report. It exists because a **results folder** wants one thing:
the energy-flow diagram for each planning horizon, at the level you read the
system on and at the level the extraction filters on. The QA command answers
that too, but only alongside per-year balance / coverage / ratio CSVs, the
integrity verdict and fifteen per-category neighbourhood charts.

| | `qa` | `sankey-pages` |
|---|---|---|
| Output | one `qa_report.html` + ~20 CSVs per year | one HTML per (year × level) + an index |
| Charts | whole-system + top-15 export neighbourhoods | whole-system only |
| Year selection | slider inside one page | one page per year, cross-linked |
| Aggregation levels | one per invocation | all requested levels from one parse |
| Purpose | is the extraction right? | what does TIMES do in 2030? |

The diagrams themselves are **the same object**: the link builders
(`aggregate_system_flows`, `prepare_system_sankey_links`), the commodity-hub
collapse, netting, export colouring and hover text are imported from `qa.py`
unchanged, so a page and its QA chart cannot drift apart. What the module adds is
the loop order — parse the `.vd` once, tag each year once, render every level
from that — and the page furniture (cross-links, level explanation, provenance
footer).

`Aggregation Level 2` appears as `mapping` in file names, its documented alias.
The index is written **last**, after every page, so a caller can use it as the
single sentinel for the whole set; pypsa-wal's cluster script does.

Used by pypsa-wal's `build_times_sankey` rule, which writes into each scenario's
`results/<run>/html/` next to its PyPSA report — see
`pypsa-wal/docs/times-sankey.md`.

#### QA report output

The `times-pypsa qa` command (see [CLI usage](#cli-usage)) writes `qa_report.html` (interactive Sankeys with year timeline and flow-netting toggle) plus per-year CSVs `qa_*_{year}.csv`.

##### Views

| View | Content |
|------|---------|
| **A** | Whole TIMES energy flows (selected `--agg-level`; year slider + netting toggle) |
| **B1…Bn** | Per-category export neighbourhoods (top 15 categories by PJ; matched + n−1 + n+1) |
| Tables | Inflow/outflow ratios · soft-link reconciliation · anchoring ledger · coverage + gaps · consistency checks (collapsed) |

The standalone whole-neighbourhood Sankey (formerly view B) was **removed**
(2026-07-25): view A already shows every flow with the same colouring, and the
per-category views B1…Bn give the export detail at a resolution that is actually
readable. Dropping it also removed ~⅓ of the report's HTML weight.

##### Tables: what to look at first

1. **Inflow / outflow ratios** — ΣVAR_FOut ÷ ΣVAR_FIn per process, at both TIMES
   and aggregated (Sankey-node) resolution. One table, three readings:
   *efficiency* (boilers / power plants, 0.3–1.0), *COP* (heat pumps and TIMES
   service accounting, > 1), and *energy balance* (`balance = outflow − inflow`,
   which must be ≤ 0 for any real conversion). Only energy-carrier flows count;
   `unmapped_in` / `unmapped_out` report what was dropped for lack of a commodity
   mapping — a non-zero `unmapped_in` is the usual reason a ratio looks
   impossible. `exported_in` / `exported_out` show where each PyPSA demand is
   picked off. A `reading` column labels each row (`conversion`,
   `COP / service accounting`, `mostly source (input ≪ output)`,
   `final demand / sink`, `high losses`, `ratio distorted (unmapped FIn)`).
2. **Soft-link reconciliation** — per sector, the PJ the extraction rules
   **tagged** vs the PJ the Sankey actually **colours**. `coloured > tagged` is
   always a defect (energy coloured that no rule matched); a small negative gap is
   expected (tagged mass ending in a magenta `U ·` sink). See
   [aggregation.md](aggregation.md#3-reconciliation-does-the-sankey-colour-what-the-rules-tagged).
3. **Anchoring ledger** — every fuel-delivery gateway with its soft-linked
   `VAR_FIn` PJ, its anchorable output PJ, the ratio, and whether the highlight
   was moved. `anchored=False` means it was deliberately left upstream to keep
   coloured energy conserved.
4. **Coverage + gaps** — exported energy per PyPSA category, and demand-sector
   `VAR_FIn` matched by no rule.
5. **Consistency checks** (collapsed `<details>`) — double-count, parent–child,
   empty rules, Comnet balance, loops, topology. These are pass/fail; each has a
   companion CSV.

##### Companion CSVs

- `qa_flows_{year}.csv` — tagged annual energy flows at TIMES process/commodity resolution
- `qa_sankey_label_map_{year}.csv` — **Sankey label → TIMES code crosswalk** (see [aggregation.md](aggregation.md#mapping-sankey-nodes-back-to-times-codes))
- `qa_export_neighborhood_{year}.csv` — matched + n−1 + n+1 rows before Sankey aggregation
- `qa_export_coverage_{year}.csv` — exported energy per category (in selected units)
- `qa_io_ratios_process_{year}.csv` — **ΣFOut/ΣFIn per TIMES process** (efficiencies, COPs, energy balances, unmapped in/out, exported share)
- `qa_io_ratios_aggregated_{year}.csv` — same at the selected `--agg-level` (the Sankey's own nodes)
- `qa_export_reconciliation_{year}.csv` — **coloured Sankey PJ vs tagged export PJ, per sector**
- `qa_anchor_ledger_{year}.csv` — per gateway: soft-linked `VAR_FIn` PJ, anchorable output PJ, ratio, anchored yes/no
- `qa_node_balance_{year}.csv` — ΣFOut−ΣFIn vs `VAR_Comnet`
- `qa_commodity_residuals_{year}.csv` — post-netting commodity residuals
- `qa_loops_{year}.csv` — SCCs with >1 node after netting, energy carriers only (raw TIMES codes, so never an aggregation artefact)
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

##### `extraction_rules.csv` schema

One row per PyPSA demand category. **Every populated filter cell is applied** — a
value that matches no mapping label is a test failure, not a silent no-op.

| Column | Meaning |
|--------|---------|
| `category` | PyPSA demand name (the key in `wallon_demands_*.csv`) |
| `pypsa_sector` | End-use sector; drives Sankey export colouring |
| `parent` | This rule is a subset of that category (declares the allowed overlap) |
| `measure_at` | Where the number is read: `fuel_input` (VAR_FIn at a conversion tech), `demand_input` (VAR_FIn at the end use), `service_output` (VAR_FOut at the demand), `delivered_fuel` (VAR_FOut of a delivery tech) |
| `var_type` | `VAR_FIN` / `VAR_FOUT` |
| `process_agg` | `;`-separated Aggregation Level 2 labels |
| `carrier` | `;`-separated PyPSA Energy Carrier labels (empty = no commodity restriction) |
| `commodity_code` | Raw TIMES codes for commodities that have no carrier row; OR-ed with `carrier` |
| `expect` | `nonzero`, or `zero` for a PyPSA placeholder TIMES-WAL does not model |
| `adjust` | Post-processing corrections, e.g. `subtract:total rail;subtract_internal_transfer` |
| `note` | Why the rule looks the way it does — read this before editing |

`pypsa_sector`, `parent` and `expect` used to be hand-maintained Python literals
(`PYPSA_SECTOR_BY_CATEGORY`, `ALLOWED_OVERLAPS`, `KNOWN_ZERO_CATEGORIES`); they are
now read from these columns. Emission commodities (CO₂, GHG, SOX, …) are dropped
before any rule sums `value`.

The previous schema (`filter_type` + `filter_field_N`/`filter_values_N`) is still
read so external rule files keep working.

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

Known-zero allowlist (`expect=zero`): `ammonia`, `methanol`, `total international navigation`. Re-verified 2026-07-25: `methanol` and `total international navigation` are structurally absent from TIMES-WAL, but **`ammonia` is not** — `IAM` (Ammonia Demand, Mt) is met at 0.319 Mt/a until 2025. Zero is still correct for a different reason: the scenario retires the plant before the first coupled horizon (no activity from 2030), its 3.5–3.8 PJ of gas and 0.1–0.3 PJ of electricity are already exported inside `methane` / `electricity`, and PyPSA-Eur removes the ammonia feedstock from industrial methane before loading `ammonia`, so a non-zero value would double-count. See [aggregation.md](aggregation.md#ammonia-and-methanol-re-verified-2026-07-25). `coal` used to be on this list; after the `INDCOA00`/`INDCOK00` label unswap it is 6.80 PJ in 2030 and non-zero in every year.

#### Open points

Points of attention for the reference scenario; the full list with context is in
[aggregation.md § Open points](aggregation.md#open-points-and-points-of-attention).
Re-run `times-pypsa qa` after any mapping/rule change and re-read the QA tables.

**No open defect is left from the 2026-07-25 audit, and the two coupling
questions it left open are now closed** (details in
[aggregation.md § Audit 2026-07-25](aggregation.md#audit-2026-07-25--what-was-fixed-and-what-is-still-open)):

- **The ~0.87 PJ of commercial non-electric, non-heat fuel is soft-linked** as the new category `services other fuel`. Reading pypsa-wal showed the intended route was a dead end: `total services cooking` / `total services` are real `build_energy_totals.py` columns but **no PyPSA component reads them** (heat is built from `space` + `water` only), so substituting them would have closed the coverage table without delivering any energy. One changed line in pypsa-wal's `write_wallon_heat_demands` serves the category instead. See [aggregation.md § Tertiary fuel](aggregation.md#tertiary-non-electric-fuel-services-other-fuel-and-why-total-services-cooking-is-a-dead-end).
- **`total electricity services` will not be split further** — closed as a *no*. pypsa-wal consumes it as a single scalar that scales the whole Walloon electricity load, and there is no cooling, lighting, appliance or refrigeration carrier anywhere in the Walloon build. Data centres remain the one informational child.

One item is left for the pypsa-wal side rather than this repository: `services other fuel` and `residential cooking electricity` are emitted but only take effect once `prepare_sector_network.py` adds them to the services-heat and electricity-load sums.

#### Rule-change log

| Date | Change | Evidence | Demand CSV impact |
|------|--------|----------|-------------------|
| 2026-08-23 | **New `sankey-pages` command / `export_sankey_pages` API**: one standalone interactive Sankey per (model year × aggregation level) plus an index, for a report or results folder. Reuses the QA link builders unchanged, so the diagrams are identical; what is new is one `.vd` parse and one tagging pass per year shared by every level, and pages that carry their own navigation and provenance. `sankey_page_names()` gives the file list without reading the `.vd`, so a Snakemake rule can declare its outputs; a requested year absent from the `.vd` still gets a page saying so, rather than a missing declared output. Drives pypsa-wal's `build_times_sankey` rule (4 horizons × 2 levels in ~9 s). | `tests/test_sankey_pages.py`, `pypsa-wal/docs/times-sankey.md` | None |
| 2026-08-23 | **Single-year charts no longer render a dead year slider.** `render_interactive_sankey_section` emits the year as a caption when a chart has one year, and the init JS treats the slider as optional. Also affects `times-pypsa qa --year 2050`, where a range input with `min == max` invited dragging that did nothing. | `test_pages_render_one_year_without_a_dead_slider`, `test_multi_year_report_keeps_its_slider` | None |
| 2026-08-23 | **New `available_agg_levels(flows)`**: the levels `aggregate_flows` can actually use, i.e. those with a label column on *both* sides. The obvious check — shared columns of the two mapping CSVs — is wrong, because `load_metadata` returns a slimmed commodity frame; that is why level validation now reads the enriched flows and a mistyped `--agg-levels` fails before the `.vd` is parsed instead of mid-render. | `test_unknown_level_fails_fast` | None |
| 2026-07-25 | **The last unmatched tertiary energy is soft-linked: new category `services other fuel`.** Reading pypsa-wal first changed the answer. The intended route — a PyPSA-Eur `services cooking` / `total services` key — is a **dead end**: those columns exist in `build_energy_totals.py` but nothing in `scripts/` or `rules/` reads them, because both residential and tertiary heat are built from `space` + `water` only (`build_hourly_heat_demand.py`: `uses = ["water", "space"]`). Substituting them would have made the coverage table read 100% while the energy stayed outside the model. The category instead reads `demand_input` on `process_agg = commercial other` with the seven non-electric commercial carriers, which isolates exactly the four processes that burn something other than electricity: `COENMIX100`/`COENMIX101` (`Com.Other Energy`) and `CCOKGMX101`/`CCOKLPG101` (gas / LPG cooking). Composition in 2030: network gas 0.568, oil 0.262, LPG 0.017, wood chips 0.010, gasoline 0.005, pellets 0.004, biodiesel 0.001 PJ. **pypsa-wal's `write_wallon_heat_demands` must add it to the `BEWAL services urban decentral heat` target** (+5.7% on that load in 2050) for PyPSA to serve it; the two caveats to agree first are in aggregation.md. | re-export diff, `qa_export_reconciliation_*.csv` (Services still exactly 1.000), `qa_coverage_gap_*.csv` 325.6 → 324.7 PJ (2030), `qa_double_count_*.csv` empty | **1 new category only.** `services other fuel` = 0.223 TWh (2021) → 0.241 (2030) → 0.243 (2050). All 57 existing categories **byte-identical** in all 8 soft-link years. |
| 2026-07-25 | **`total electricity services` stays one total — question closed as a *no*.** Verified rather than assumed: pypsa-wal consumes it as the single scalar that scales the whole Walloon electricity load, and `cooling` appears in `prepare_sector_network.py` only in the EV cabin-temperature correction; there is no lighting, appliance or refrigeration carrier anywhere in the Walloon build. Splitting cooling / lighting / appliances / refrigeration would produce numbers with nowhere to go. Data centres stay the one informational child. | pypsa-wal `scripts/` audit | None |
| 2026-07-25 | **`ammonia` / `methanol` known-zero justifications corrected.** `methanol` is genuinely absent (no commodity, process or `.vd` row anywhere; Walloon chemistry is aggregated into `ICH` / `NEC` / `NEO`, whose fuels `methane` / `naphtha` / `coal` already read). **`ammonia` is not absent** — the old note said "no ammonia commodity exists in the model", but `IAM` (`Ammonia Demand`, **Mt**) is met by `IAMSTDPRO00`/`IAMSTDPRO01` at 0.319 Mt/a in 2021–2025, burning 3.48–3.80 PJ of `INDGAS` and 0.10–0.27 PJ of `INDELC`. Zero remains right for three independent reasons: the demand series stops after 2025 (no `EQ_Combal`, no `VAR_Act` from 2030, so every coupled horizon is genuinely zero); that gas and electricity are **already exported** inside `methane` and `electricity`, while pypsa-eur subtracts the ammonia feedstock from industrial methane before loading `ammonia`, so a non-zero value would double-count; and `IAM` is Mt, which `exported_unit_check` rejects. | `IAM` rows of the reference `.vd`, `AllCommodities.csv`, pypsa-eur `build_industry_sector_ratios.py` | None (both stay 0) |
| 2026-07-25 | **Industrial solar was genuinely counted twice, and is now counted once.** `INDSOL` and `RENSOL` are two consecutive links of one chain, not two names for one flow: `MINRENSOL` → `RENSOL` → `INDSOL00` (`Fuel Tech - Solar (IND)`, 1:1 adaptor) → `INDSOL` → `INDPVELC` (`PV industrial`, 1:1) → `INDELC`. The `electricity` rule read both `VAR_FIn` legs, adding the same on-site PV to industrial electricity demand twice; the efficiency-1.000 chain hid it from every ratio check. The upstream adaptor leg is dropped, keeping `PV industrial`, where the PV electricity enters `INDELC`. The resource side was never double counted — `MINRENSOL` 26.70 PJ (2030) equals ELC 18.69 + RSD 5.41 + COM 1.80 + IND 0.80 exactly. | re-export diff, `qa_double_count_*.csv` | **1 category changes.** `electricity` −0.222 TWh in 2025/2030/2035, −0.125 TWh in 2021, unchanged from 2040 (the process ends). |
| 2026-07-25 | **Commercial electricity stays one total, with data centres split out as a child.** The single `total electricity services` is correct for the coupling — pypsa-wal scales the Walloon electricity load on that one scalar and PyPSA-Eur has no cooling/lighting/appliance bus — but it is dominated by a single end-use: data centres are **32% of it in 2030 and 49% in 2050** (9.37 → 24.73 PJ, while the total goes 29.31 → 50.64 PJ). `COSEELC100`/`COSEELC101` therefore get their own `Commercial data centres` label and a new child category **`services data centre electricity`**. The 90 remaining processes (other equipment, building + public lighting, space cooling, refrigeration, electric cooking) cannot be split without further relabelling, and PyPSA has nowhere to put them. | re-export diff, `test_rule_count`, reconciliation at `custom` level | **1 new category only.** `services data centre electricity` = 0.69 TWh (2021) → 2.60 (2030) → 6.87 (2050). `total electricity services` **unchanged**; being a child, it must not be added to any pypsa-wal sum. |
| 2026-07-25 | **Loop check restricted to energy carriers; the surviving loop is understood.** 3 components → **1**: the iron & steel `MISSCR ↔ MISCST` scrap recycle is real but both commodities are `.MAT.` in Mt, and the industrial heat-recovery loop ran through unmapped `INDHTH`/`IOIHTH`. What is left is the pulp-mill cogeneration recycle (`IPPPUPCHE01` → 10.2 PJ black liquor → `CHPINDBLQIPPN00_N` → 0.57 PJ electricity + 4.63 PJ heat → back into the mill) — a physical autoproducer feedback. Loop detection runs at raw TIMES process/commodity resolution, so a loop can never be an aggregation artefact; the earlier "or aggregation artefacts" wording was wrong. | `qa_loops_*.csv`, `test_balances.py` | None |
| 2026-07-25 | **The Comnet residuals were an artefact of a missing value read as zero.** `VAR_Comnet` in the reference `.vd` covers **only** emission / pollutant aggregates — 91 codes, no energy carrier at all, not even `ELCHIG` or `GASNAT` — so the check was comparing every energy commodity against an implicit 0 and reporting its whole net flow (77 PJ in 2030 / 95 PJ in 2050, dominated by `.DEM.` service commodities `TAIF`, `COSE`, `RLIG`, `RCOK`, …). `commodity_balance_vs_comnet` now compares only commodities `VAR_Comnet` actually reports, and the QA call drops the PJ-only filter so the emission aggregates are checked: **all 71 (2030) / 73 (2050) balance exactly.** | `test_comnet_balance_only_compares_reported_commodities`, `test_comnet_balance_holds_where_comnet_is_reported` | None |
| 2026-07-25 | **Aviation now reads the jet fuel, not the service.** pypsa-wal's `add_aviation` puts `total international aviation + total domestic aviation` straight onto a `kerosene for aviation` load supplied from the oil bus, so PyPSA wants the fuel; both rules switch to `measure_at=demand_input` / `VAR_FIN` on `Kerosene - Jet Fuels for transport`. Same convention as `total domestic navigation`, and no `VAR_FOut` service rule is left outside heating. | re-export diff, `test_no_exported_flow_carries_a_non_pj_unit` | **None — identical values**, because TIMES gives all four aviation processes efficiency exactly 1.000. The change removes the latent risk of silently exporting a service if that ever stops holding. |
| 2026-07-25 | **Over-colouring fixed: the commodity collapse now keys links on `export_status`.** A node pair carrying both soft-linked and untagged energy becomes two ribbons (coloured + grey) instead of one promoted by `merge_export_statuses(any_exported=True)`; `net_collapsed_process_links` nets per status for the same reason. Services and Residential now reconcile **exactly** (`gap_pj` = 0 in 2030 and 2050), Industry exactly in 2050, and no sector is over-coloured in any year. The remaining negative gaps are the H₂-electrolyser / EV-storage conversion losses, which correctly stay outside the highlight. | `test_export_reconciliation_matches_tagged_energy` (new per-sector `coloured ≤ tagged` assertion), `test_net_collapsed_process_links_nets_reciprocal` | None (display only) |
| 2026-07-25 | **`coal` and `coke` were genuinely swapped — in the mapping, not in the category names.** `INDCOA00`'s TIMES *Description* is `Fuel Tech - Hard Coal (IND)` but its Aggregation Level 2 said `Fuel Tech - Coke (IND)`, and `INDCOK00` the mirror image; the two labels are now unswapped, so `coal` reads hard coal + lignite and `coke` reads coke. `naphtha` turns out **not** to be misnamed: it is PyPSA-Eur's industrial *oil* bus (`naphtha for industry`), and pypsa-wal's own non-TIMES branch fills it from "non-energy oil feedstock + total industrial oil" — exactly what the rule reads. Its non-oil legs were moved to the matching carriers: `Non-energy` GASNAT (8.28 PJ, 2021–2025) → `methane`, COAHAR (0.03 PJ) → `coal`. | `INDCOA00`/`INDCOK00` Description vs label, re-export diff | **4 categories change.** `coal` ↔ `coke` swap (2050: `coal` 0 → 1.854 TWh, `coke` 1.854 → 0; `coke` now correctly ends in 2035 while coal persists). `naphtha` −2.309 TWh and `methane` +2.300 TWh in 2021–2025 only. |
| 2026-07-25 | **`residential cooking` cleaned up.** `RCOKCOA100`/`RCOKELC100` (`Rsd.Cooking.{COA,ELC}.00.Stove.`) carried `residential other` while their `.00.Stove.` gas/LPG/log siblings carried `residential cooking` — the same-technology-two-labels defect again → both relabelled. `INFPRCCOK01` is `IND.Other Non Ferrous Metals.Process Heat.COKE.01` (it outputs `INFPRC`), mislabelled `residential cooking` on a `COK`/cooking name clash → `Industry`. New child category **`residential cooking electricity`** exposes the electric split, because pypsa-wal scales the Walloon electricity load on `total electricity residential` + services + rail. | re-export diff, `test_rule_count`, `test_parent_child_heat_sums` | **3 categories change.** `residential cooking` +0.62 TWh (2021) → +0.04 (2035); `total electricity residential` −0.62 → −0.04; new `residential cooking electricity` (0.62 TWh 2021, 0.43 2050). **`prepare_sector_network.py` in pypsa-wal must add `residential cooking electricity` to its electricity-load sum.** |
| 2026-07-25 | **`electricity road` / `total road` now meter EV charging at the charger input.** The two "measurement points" turned out to be two *physically distinct* grid draws, not the same energy twice: `ELCLOW` → `Fuel Tech - Electricity (TRA)` serves electric trucks / rail / two-wheelers (37.0 PJ 2050) while `RSDELC`/`COMELC` → home/work chargers serve the car fleet (25.0 PJ). Only the second was metered downstream (`BATELCIN`, `TRA_STG_PJ_GW`), so the charger loss escaped. `process_agg` `TRA_STG_PJ_GW` → `EV charger` in both rules, making both legs low-voltage grid-side. | re-export diff (only these 2 categories move), `test_extraction_coverage.py` | **2 categories change.** `electricity road` +0.348 TWh (2050) / +0.072 (2030); `total road` the same. The delta is exactly the 1.25 PJ charger loss. |
| 2026-07-25 | `sankey_overview` label fixes: `ERNW_PV-*` (3) and `COMSOL00`/`RSDSOL00` (2) carried `PV` at *overview* level while `custom` is the level that splits generation — they now read `Power plants`, and `custom` still gets `PV` from `_generation_split_label`. Restores the ≤20-node overview budget after `Exports` was added. | `test_bundled_mapping_csvs_share_aggregation_columns` (12 process + 8 commodity = 20) | None |
| 2026-07-25 | **Black liquor exclusion made explicit, and its unit corrected `MT` → `PJ`** (VEDA declares `INDBLQ` `.NRG.`/PJ; the audit had confused it with the `.MAT.` `MBORES`). It stays out of `solid biomass` on purpose: it is produced and burnt inside the pulp mill (`IPPPUPCHE*` → `CHPINDBLQIPPN00_N` / `BBLQH2G110`), never passes a `Fuel Tech … (IND)` gateway, and PyPSA cannot source it from the wood potential. Stated in the rule's `note`. | `test_mapping_units_match_the_veda_dictionary` (INDBLQ was the only unit disagreement in the whole mapping) | None |
| 2026-07-25 | **`Exports` split out of `Imports & trade`** on `custom` / `sankey_overview`. The "flows into imports" seen in 2022 were the export legs (`EXPBIOPEL` 10.9 PJ, `EXPBIOLOG` 1.4, `Transfo_Exp`→`EXPELCHIG` 28.7) sharing a node with the importers; the remaining `Imports & trade` inflow is the `IMPELCHIG`/`IMPELCOFFWINBE` → `Transfo_Imp` self-loop, which the collapse drops. | `test_export_processes_are_a_separate_sankey_node`, `qa_sankey_label_map_2022.csv` | None (display only) |
| 2026-07-25 | **`total domestic navigation` corrected to final energy**: reads the ships' diesel `VAR_FIn` (carrier `total energy for transport`) instead of the `TNDF` **Btkm** activity `VAR_FOut`; the same diesel is subtracted from `total road`, which measures it at the fuel tech. Subtractions are now declared in the `adjust` column rather than hardcoded. | `test_navigation_reads_the_fuel_not_the_activity`, `test_no_exported_flow_carries_a_non_pj_unit`, `test_declared_subtractions_remove_downstream_double_counts` | **2 categories change.** `total domestic navigation` 1.617 → **0.572 PJ** (2030), 1.898 → 0.672 (2050); `total road` −0.57 (2030) / −0.67 PJ (2050). No exported commodity is non-PJ in any year now. |
| 2026-07-25 | **`extraction_rules.csv` rewritten**: `category,pypsa_sector,parent,measure_at,var_type,process_agg,carrier,commodity_code,expect,adjust,note`. Every populated filter is applied (the old schema dropped `filter_values_2` on 9 of 55 rules); `pypsa_sector`/`parent`/`expect` replace the `PYPSA_SECTOR_BY_CATEGORY`/`ALLOWED_OVERLAPS`/`KNOWN_ZERO_CATEGORIES` Python literals. Legacy schema still read. | `test_every_declared_filter_value_resolves`, `test_rule_metadata_columns_are_complete` | **None — all 46 emitted files byte-identical.** |
| 2026-07-25 | Emission commodities (CO₂/GHG/SOX/…) dropped before any rule sums `value`; an unknown filter field now raises instead of applying no filter. | `test_emission_commodities_never_reach_a_demand`, `test_unknown_filter_field_raises` | None (closes a latent 2000 PJ error) |
| 2026-07-25 | Dropped `Aggregation Level 1` + `PyPSA technology` (nothing read them) and the stray `Notes` column; commodity `Unit` corrected for `TNDF` (BTKM) and `INDBLQ` (MT); new `exported_unit_check` flags any exported non-PJ commodity. | `test_bundled_mapping_csvs_share_aggregation_columns`, `test_exported_unit_check_flags_non_pj_commodities` | None |
| 2026-07-25 | **Mapping label fixes**: 16 commercial electric heaters (`…ELC101/ELC201`) `commercial other`/`Commercial Biomass boiler` → `Commercial electrical stove` (the existing `…ELC100` vintage already had it); 3 `COMSOL` processes → `Commercial solar thermal`; 6 commercial retrofits `Dum_Retrofit_Commercial` → `Retrofitting improvements`. | `qa_export_coverage_*.csv`, re-export diff | **7 of 55 categories change.** 2050: `services electric heater` +6.32, `BEWAL services urban decentral heat` +6.04, `total electricity services` −6.15, `services biomass boiler` −0.29, `services solar thermal` +0.02 PJ; `retro` +0.05→+1.59 PJ over 2022–2050. Plus 2670 MW of commercial electric heating capacity now in `heating_capacities_2050.csv`. |
| 2026-07-25 | **Anchoring is now conservation-checked** (`ANCHOR_BALANCE_TOLERANCE` 10%): a gateway is anchored only if its outputs carry the same energy as its soft-linked input, else the highlight stays upstream; gateway→gateway chain hops are never highlighted. Fixes `Fuel Tech - H2` colouring 1.20 PJ of industrial hydrogen as Transport *and* Industry. | `test_anchoring_conserves_highlighted_energy`, `test_no_double_count_along_a_gateway_chain`, `qa_anchor_ledger_*.csv` | **None — `wallon_demands_*.csv` byte-identical to the previous commit for all 8 soft-link years (verified by re-export).** Applies to every 2026-07-25 row below. |
| 2026-07-25 | **New reconciliation**: coloured Sankey PJ vs tagged export PJ per sector (`export_reconciliation`, `qa_export_reconciliation_*.csv`) — the check the suite lacked. | `test_export_reconciliation_matches_tagged_energy` | None |
| 2026-07-25 | **New inflow/outflow ratio tables** (`process_io_ratios`) at TIMES and aggregated resolution, replacing most of the old table dump; report keeps view A + per-category B1…Bn, drops the whole-neighbourhood Sankey, and collapses pass/fail checks into `<details>`. | `test_balances.py`, `test_qa_html.py` | None |
| 2026-07-25 | **QA report ~17× faster** (2-year run 179 s → 10.5 s; full 8-year run ~30 s) and HTML 4.6 MB → 3.0 MB. Main cause was a `DataFrame.attrs` deepcopy storm: `category_keys` (~10⁵ tuples) was re-deepcopied by pandas `__finalize__` on every slice/groupby of the tagged frame (143 s of the 179 s). Also: cache the system aggregation per (year, netting) instead of recomputing it per diagram, vectorize flow-key building, single-pass category masks, drop a throwaway link-count pass. | `CategoryKeyIndex`, `aggregate_system_flows`, cProfile before/after | None |
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
