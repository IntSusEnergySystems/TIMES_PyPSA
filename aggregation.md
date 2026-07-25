# Aggregation levels and Sankey colouring

Detailed reference for TIMES → PyPSA Sankey aggregation (`--agg-level`), commodity-hub collapse, export colouring, and the `custom` working level. CLI overview and install remain in [README.md](README.md).

## Aggregation levels (Sankey / QA only)

Extraction itself is unchanged (filters on Aggregation Level 2 labels). Sankey / QA aggregation is selected by a **single shared CSV column name** (process mapping × commodity mapping).

| Level (`--agg-level`) | Process nodes | Commodity nodes | Typical node count | Use |
|-----------------------|---------------|-----------------|--------------------|-----|
| **`Aggregation Level 2`** (default; alias `mapping`) | Aggregation Level 2 | Aggregation Level 2 (= PyPSA Energy Carrier) | hundreds | Export neighbourhood / detailed QA |
| **`custom`** | Export-touching Aggregation Level 2 (+ friendly renames); else overview / `(other)` | Export-touching commodity codes (specific labels); else `{family} (context)` | **~45** after collapse | **Working level**: readable whole-system + sector-coloured soft-link paths |
| `Sector` (alias `L0`) | Sector codes | Sector codes | ~15 | Coarse sector check |
| **`sankey_overview`** | Overview process clusters | Overview carriers | **≤20** | Readable whole-system Sankey |
| `L2` | `process_code` | `commodity_code` | thousands | Fine drill-down (no collapse) |

**No opaque placeholders.** Empty mapping cells fall back to TIMES Description, then code — never `Unknown`.

**Why these four.** `Aggregation Level 2` is load-bearing — it is the grain `extraction_rules.csv` filters on, so it cannot be dropped. `custom` is the level to actually look at: for 2030 it renders 247 links / 94 nodes against Level 2's 948 / 132, and no exported link spans more than one PyPSA sector, so every soft-linked flow stays isolable and correctly coloured. `sankey_overview` is not redundant with `custom`: `custom` reads it to build its `… (context)` buckets, so deleting the column would fall the collapse back to description heuristics. `Aggregation Level 1` and `PyPSA technology` were removed on 2026-07-25 — no code read them (`pypsa-wallon` assigned `PyPSA technology` then immediately overwrote it on the next line).

## Custom aggregation levels

Sankey readability is controlled by mapping CSVs, not by hard-coded Python clusters. To define a new level:

1. **Pick one column name** (e.g. `sankey_overview`, `my_sector_v2`). The same header must exist in:
   - `data/mapping_processes.csv`
   - `data/mapping_commodities.csv`
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

## Process nodes and commodity flows (Plotly)

Plotly Sankey diagrams do not support different node shapes for “commodity as a
flow”. This package therefore **collapses commodity hubs** after aggregation:

1. **Balanced commodity** (Σ `VAR_FOut` ≈ Σ `VAR_FIn`): remove the commodity node
   and allocate energy proportionally as **process → process** links. The commodity
   name is kept as a link attribute (hover: “Commodity flow: …”).
2. **Expected residual** (final-demand FOut-only commodity, or FIN only on non-PJ
   processes visible in optional `reference_flows`): log at **INFO**, collapse the
   matched portion if any, and route the residual to a magenta node **named after
   the demand process**. Hover text explains the sink (DEM output or excluded
   Activity unit such as `MM2` / `BVKM`).
3. **Unexplained small imbalance** (relative error &lt; 10%): log a **warning**,
   collapse the matched portion, and route the residual to an `Unbalanced <…`
   magenta node.
4. **Unexplained large imbalance** (≥ 10%): log an **error**, still generate the
   Sankey the same way (matched collapse + residual artificial node).

Artificial residual nodes use kind ``imbalance``:

- **Expected sinks:** label = demand process name (e.g. `international aviation`,
  `Building4F`); display prefix `U · …`; tooltip describes final demand / non-PJ
  exclusion.
- **Unexplained:** label mirrors the warning/error text, e.g.
  `Unbalanced ≥10%: Oil products (FOut>FIn, 78.9%)`
- **Colour:** magenta family (distinct from green process nodes)

Self-loops (same process both producing and consuming a commodity) are dropped
because Plotly cannot draw them.

**Colours:**

Exported links are coloured **by PyPSA end-use sector** (see
[Export strategy](#export-strategy-per-sector-colours--demand-inflow-anchoring)),
not by a single blue. The old light-red *mixed* status never occurs on the real
`custom` data (verified across all years, 2021–2050) and is no longer used for
colouring; purple stays reserved as a *double-count* warning that should never
appear.

| Element | Colour | Meaning |
|---------|--------|---------|
| Process / commodity **nodes** | green family | ordinary Sankey nodes (no P/C prefix; commodities are flows after collapse) |
| Imbalance **nodes** | magenta family | `U ·` demand / residual hubs (expected or unexplained) |
| Link exported → **Industry** | blue | soft-linked flow feeding industry demand |
| Link exported → **Transport** | orange | road / rail / aviation / navigation |
| Link exported → **Residential** | red | residential electricity + heat + retrofit |
| Link exported → **Services** | teal | commercial / tertiary electricity + heat |
| Link exported → **Agriculture** | brown | agriculture electricity / heat / machinery |
| Link context | grey | unrelated to PyPSA export |
| Link double-count | purple | **both** FOut and FIn endpoints exported (should not occur) |

**Plotly limitation:** Sankey links only support a **single solid colour** per ribbon — there is no source→target gradient.

Internal node ids are typed (`process::Label` / `imbalance::Label`) so names never
collide. Optional bipartite view (commodity nodes kept) is still available via
`collapse_commodities=False` on the prepare helpers.

## What PyPSA must receive: final energy demand

**The rule: each exported category is the sector's *final energy demand* — the
energy the end-use device consumes, in PJ.** Not the service it delivers (Btkm,
Mm², vkm), and not the primary or transformed energy upstream of it. `measure_at`
in `extraction_rules.csv` records which point each rule reads:

| `measure_at` | Reads | When it is the final energy |
|--------------|-------|------------------------------|
| `demand_input` | `VAR_FIn` at the end-use device | **Default.** The fuel the ship, car, train or appliance burns |
| `fuel_input` | `VAR_FIn` at a conversion tech that serves a whole sector | When the tech is a pure fuel-delivery step, so its input equals what the sector consumes |
| `service_output` | `VAR_FOut` at the demand | **Heating only** — PyPSA models the heat bus, so it wants useful heat out of the boiler/heat pump, not the boiler's fuel in |
| `delivered_fuel` | `VAR_FOut` of a delivery tech | A carrier handed to a sector (industrial H₂) |

Two consequences worth stating:

- **A `VAR_FOut` on a non-heat rule is a red flag.** It is usually the *service*,
  which is not energy and often not even in PJ. `total domestic navigation` read
  `TNDF` — a **Btkm** activity — as if it were energy (1.62 PJ in 2030 against
  0.57 PJ of diesel actually burnt, a 283% implied efficiency). It now reads the
  ships' diesel, and the two aviation rules read the jet fuel. Since 2026-07-25
  the only `VAR_FOut` rules left are the heat rules (carrier `Heat`) plus
  `hydrogen` (`delivered_fuel`), enforced by
  `test_var_fout_rules_are_heat_or_delivered_fuel`.
  `exported_unit_check` fails the build if any exported commodity is not declared PJ.
- **`fuel_input` rules sit upstream of several sectors, so they over-collect.**
  One road diesel tech (`TRADST00`) supplies road vehicles, rail *and* inland
  ships from the same `TRADST` pool, so `total road` contains all three. Each
  downstream category is declared in the upstream rule's `adjust` column
  (`subtract:total rail;subtract:total domestic navigation`) and subtracted.

## Export strategy: per-sector colours & demand-inflow anchoring

The Sankey exists to check that **every** soft-linked flow is accounted for and
that **nothing is double-counted**. Two changes make the exported flows read
clearly (implemented in `assign_export_sectors`, applied inside
`prepare_system_sankey_links` / `prepare_export_sankey_links`):

### 1. Colour exported links by PyPSA sector

Every `extraction_rules.csv` category maps to one PyPSA end-use **sector**
(`category_sector`, table `PYPSA_SECTOR_BY_CATEGORY`):

| Sector | Colour | Categories (examples) |
|--------|--------|-----------------------|
| **Industry** | blue | ammonia, coal, coke, hydrogen, low-temperature heat, methane, methanol, naphtha, solid biomass, electricity |
| **Transport** | orange | total road, electricity road, hydrogen road, total/electricity rail, domestic/international aviation & navigation |
| **Residential** | red | total electricity residential, BEWAL residential heat, residential * boilers/heat-pump, residential district heating, retro |
| **Services** | teal | total electricity services, BEWAL services heat, services * boilers/heat-pump/district-heating |
| **Agriculture** | brown | total agriculture (+ electricity / heat / machinery) |

Each collapsed link carries an `export_sector` derived from its matched
categories (`link_sector`; when categories span sectors, the
`PYPSA_SECTOR_ORDER` priority wins). Context links stay grey. This **replaces
the former single blue** and the light-red *mixed* status, which was verified
never to occur on the real `custom` data (see
[test](#tests-guarding-the-export-strategy)).

### 2. Anchor soft-linked fuel inputs to the demand inflow — only when energy is conserved

Previously an exported **`VAR_FIn`** (a fuel/electricity *input* into a
conversion process) was highlighted on the *upstream* fuel-supply link, so
industry / transport fuel exports appeared far to the **left** (near imports /
power plants) while heat / aviation `VAR_FOut` exports appeared on the **right**
(at the demand). The soft-links were scattered across the diagram.

`assign_export_sectors(..., anchor=True)` moves each such export onto the
**demand inflow**:

- A **gateway** is a fuel-delivery / storage / geothermal process
  (`_is_anchorable_gateway_label`: `Fuel Tech …` / `Fuel tech …`,
  `EV battery storage`, `Geothermal (IND)`) whose `VAR_FIn` is soft-linked.
- The highlight is removed from the gateway's **input** link (upstream fuel
  supply → grey) and placed on the gateway's **output** link(s) (gateway → end
  use), coloured by the gateway's sector.
- `VAR_FOut` exports (boiler heat, aviation, hydrogen production, …) are already
  demand-facing and are left in place, just coloured by sector.
- Demand-side `VAR_FIn` exports (appliance electricity, `Industry` ammonia,
  agriculture, rail, hydrogen-road vehicles) already terminate at the end use
  and are kept.

#### The conservation guard (added 2026-07-25)

Moving a highlight is only legitimate if the destination carries the **same
energy** as the source. Anchoring is therefore **conditional**: for each gateway
the code compares its soft-linked `VAR_FIn` PJ against the PJ on its anchorable
output links, and anchors only when the ratio is within
`ANCHOR_BALANCE_TOLERANCE` (**10%**). Otherwise the highlight is **left on the
fuel-supply link** — the export stays visible and no coloured PJ is invented or
lost. Every decision is written to `qa_anchor_ledger_{year}.csv` and shown in the
report, so a skipped anchor is visible instead of silent.

The guard rejects three failure modes that the unconditional version had:

| Failure mode | Example (reference scenario) | Effect before the guard |
|---|---|---|
| **Lossy / multi-output process** wrongly treated as a 1:1 delivery tech | `Fuel Tech - H2` bundles the electrolyser + tank + delivery: 2.446 PJ soft-linked electricity in, 1.68 PJ anchorable out (ratio 0.69) | 1.20 PJ of *industrial* hydrogen was coloured **Transport** on `Fuel Tech - H2 → hydrogen for industry`, then coloured **Industry** again one hop later — the same molecules counted twice in two sectors |
| **Partly-tagged input** → all outputs coloured | any gateway where only part of `VAR_FIn` is soft-linked | coloured PJ exceeded tagged PJ (energy invented) |
| **Threshold truncation** dropping some outputs | `Fuel Tech - Electricity (TRA)` at the per-category views' 0.1 PJ threshold | up to −19% of the highlight silently lost |

Gateways that *do* pass are genuinely 1:1 — the ledger shows ratio `1.000` for 15
of 17 in 2050, `0.98` for `EV battery storage` (round-trip loss) and `0.956` for
`Fuel Tech - Biodiesel (TRA)`. For those, the highlighted quantity is preserved
and every soft-link enters an end-use node coloured by sector: industry fuel
exports on `Fuel Tech (IND) → Industry (other)`, transport fuels on
`Fuel Tech (TRA) → Cars / Road Freight`.

#### Gateway chains are not highlighted

A link `gateway → gateway` is an **internal transfer**, not a demand inflow.
Colouring it *and* the downstream demand inflow would count the same fuel twice —
precisely the quantity `road_internal_transfer_pj` already subtracts from the
`total road` demand. Such links are excluded from the anchor target set, and the
chain's own export is demoted when its target gateway was anchored. Biodiesel
blended into diesel is now coloured exactly once.

Generation nodes (PV, wind, power plants, CHP) are **never** anchored across
their heterogeneous outputs — `_is_anchorable_gateway_label` is restricted to
fuel-delivery techs so a small industry-electricity rule via `PV industrial`
cannot recolour all of PV's grid output. Note this predicate is a **prefix test
on the display label** (`Fuel Tech …` / `Fuel tech …`, `EV battery storage`,
`Geothermal (IND)`): 59 labels in `mapping_processes.csv` match it, including
power-sector fuel supply (`Fuel Tech - Nuclear`, `Fuel tech · methane`, …) that
is *not* a 1:1 demand gateway. Those are harmless today only because their inputs
carry no soft-link tag — the conservation guard, not the predicate, is what keeps
them safe. Prefer tightening the predicate over relying on that.

### 3. Reconciliation: does the Sankey colour what the rules tagged?

`export_reconciliation(tagged, links)` compares, per PyPSA sector, the
soft-linked PJ the **extraction rules matched** against the PJ the diagram
actually **draws** as an exported ribbon. Until 2026-07-25 nothing in the
codebase checked this: every other QA table validates the rules, none validated
that the Sankey shows what the rules matched — so the double-counts above were
invisible.

Read it as:

- **`coloured < tagged`** — a soft-link the diagram does not show. A small
  negative gap is expected: some tagged mass ends in a magenta `U ·` sink rather
  than a process→process ribbon.
- **`coloured > tagged`** — energy coloured that **no rule matched**. Either an
  untagged flow merged onto an exported process pair, or a fuel coloured twice
  along a chain. This direction is always a defect.

Reference scenario (`custom`, netted, PJ), after the 2026-07-25 status-keyed
collapse:

| Sector | 2030 tagged | 2030 coloured | 2050 tagged | 2050 coloured |
|--------|------------:|--------------:|------------:|--------------:|
| Industry | 117.28 | 116.48 | 117.84 | **117.84** |
| Transport | 136.66 | 134.06 | 113.22 | 108.69 |
| Residential | 98.12 | **98.12** | 103.67 | **103.67** |
| Services | 48.35 | **48.35** | 66.40 | **66.40** |
| Agriculture | 5.35 | 5.12 | 5.35 | 5.12 |
| **TOTAL** | 405.76 | 402.14 (99.1%) | 406.48 | 401.72 (98.8%) |

**No sector is over-coloured any more.** The Services excess (+0.15 TWh 2030 /
+0.27 TWh 2050) came from `merge_export_statuses(any_exported=True)`, which
promoted a whole process pair to `exported` when *any* commodity collapsed onto
it was exported — so untagged `Commercial cooling` output riding the
`Commercial Heat pump → Commercial buildings` pair got coloured too. The fix is
to make `export_status` part of the collapse key, so such a pair becomes **two
ribbons** (a coloured one carrying the tagged PJ and a grey one carrying the
rest) instead of one promoted ribbon. `net_collapsed_process_links` nets per
status for the same reason: cancelling coloured mass against untagged mass would
move PJ no rule matched. Link count is essentially unchanged (249 vs 247 in 2030).

The remaining gaps are all negative and all understood: the Transport shortfall
is the H₂-electrolyser and EV-battery conversion loss that correctly stays
outside the highlight, and small residuals are tagged mass ending in magenta
`U ·` sinks rather than a process→process ribbon. The near-100% total previously
quoted was a **net** of offsetting errors in both directions — which is why the
table is per-sector.

### Display-only: PyPSA demand CSVs are unchanged

Colouring and anchoring are **Sankey display only**. Extraction still filters on
`Aggregation Level 2` / `process_agg` + `pypsa_carrier` in `extraction_rules.csv`.
The exported `wallon_demands_*.csv` are **byte-identical** before and after these
changes (verified for every soft-link year 2021–2050).

### Tests guarding the export strategy

`tests/test_export_sectors.py`:

- every extraction category resolves to a sector; sectors have distinct colours;
- `assign_export_sectors` anchoring (synthetic): gateway input → context, output
  → exported + sector; demand-side / `VAR_FOut` exports kept;
- on the reference (toy) scenario, for **all** years: **no `mixed` / no
  `double_count`** link, and **every exported link has a sector**;
- `test_anchored_gateway_inputs_are_greyed` — a gateway that *was* anchored keeps
  no exported input (a *skipped* gateway legitimately does);
- `test_anchoring_conserves_highlighted_energy` — every anchored gateway's
  in/out ratio is within `ANCHOR_BALANCE_TOLERANCE`;
- `test_no_double_count_along_a_gateway_chain` — no fuel is coloured on both a
  chain hop and the downstream demand inflow;
- `test_export_reconciliation_matches_tagged_energy` — coloured PJ is 90–102% of
  tagged PJ and the per-sector rows sum to the total;
- `test_anchor_ledger_is_reported_for_every_gateway` — every gateway decision is
  recorded with its energies.

`tests/test_balances.py` covers `process_io_ratios` / `classify_io_ratio`: COP vs
efficiency vs source readings, unmapped-input flagging, aggregated grouping,
throughput ordering, and empty-input safety.

## Designing `custom` (working level)

`custom` is the **editable working aggregation** for export QA. Goal: a **readable** whole-system Sankey that still shows PyPSA-exported flows as **sector-coloured** links at soft-link resolution.

Extraction filters on **Aggregation Level 2** (`process_agg` in `extraction_rules.csv`), not on individual process Descriptions. Description-level nodes are therefore over-detailed for the soft-link objective and make the diagram unreadable once small links are kept (threshold 0).

### Runtime rules (when flows are export-tagged)

`aggregate_flows(..., level="custom")` applies `refine_custom_labels_for_readability` on top of the CSV `custom` column:

| Side | Keep individual | Collapse |
|------|-----------------|----------|
| **Processes** | **Supply-chain roles** (parallel, same direction): `Imports & trade`, `Local production` (MIN* + Wallonia biogas), `Fuel conversion`, `Power plants`, electricity storage / EV chargers. **Plus** Aggregation Level 2 labels on any exported row (soft-link grain, with friendly renames). | Remaining context → `End-use fuel tech`, sector `(other)`, … — never merge upstream primary with downstream end-use |
| **Commodities** | Export-touching codes keep one specific label for all rows of that code | Other carriers → `{family} (context)` |

After commodity-hub collapse, **reciprocal process↔process ribbons are netted** when the netting toggle is on (`net_collapsed_process_links`), so A→B and B→A from coarse hubs do not appear as loops. Both the collapse and the netting key on `export_status`, so a coloured ribbon is never netted against a grey one.

**Friendly process renames**

| Aggregation Level 2 | `custom` display |
|---------------------|------------------|
| `residential other` | Household electrical appliances |
| `commercial other` | Commercial electrical appliances (default); CSV subclasses kept: **Commercial cooling**, **Commercial lighting**, **Commercial cooking** |
| `Commercial data centres` | **Commercial data centres** (`COSEELC100/101`, split out 2026-07-25) |
| `Retrofitting improvements` | Building retrofits |
| `hydrogen imports` | imported H2 delivery |
| `Buildings: built area` (`RDW_*`) | **Residential buildings** |
| `Building Existing/New *` (`COM_CBAT_*`) | **Commercial buildings** |
| `residential cooking` | Residential cooking |

**Supply-chain roles (do not merge across steps)**

| Label | Meaning |
|-------|---------|
| Imports & trade | `.IMP.IRE.` / import processes (**inflow only**) |
| Exports | `.EXP.IRE.` processes + `Transfo_Exp` — a **sink**: they consume domestic energy (see note below) |
| Local production | `.MIN.IRE.` domestic potentials **and** Wallonia biogas methanisation/upgrading (`BWBIOGAZ100`, `BWSUPGZH100`, digestor heat) — stands in for Mt feedstocks off the energy Sankey |
| Fuel conversion | SUP synthesis / blending / H₂ (formerly misnamed “Fuel refining”) |
| Power plants | Generation (thermal, nuclear, …) — **excluding** PV / onshore wind / electricity storage |
| Grid / pumped / household / EV battery storage | Electricity STG with charge FIn + discharge FOut (kept out of Power plants) |
| EV chargers | Home/work chargers (`TCHARG*`) — electricity → `BATELCIN` (context; not soft-linked) |
| PV | Utility + rooftop / sector PV (`ERNW_PV*`, `*PVELC`, `ELCSOL00` / `RSDSOL00` / `COMSOL00`) |
| Onshore wind | Onshore turbines (`ERNW_WINON*`, `ERNW_Eolien*`, wind fuel-tech ELC) |
| CHP | Combined heat & power plants (`Type=CHP`, public/industrial/tertiary CHP) |
| District heating | Network heat exchangers (`District heating`, `Commercial Heat Exchanger`) |
| Fuel tech · {category} | End-use fuel-tech PRE, **split by extraction_rules commodity** (electricity, methane, coal, …) |
| Building retrofits | Envelope retrofit techs + `Dum_Retrofit*` options (demand-side efficiency; see note below) |

Offshore-wind imports (`IMPELCOFFWIN*`) stay under **Imports & trade** (outside Wallonia).

### Imports vs exports (why energy used to flow *into* the import node)

Until 2026-07-25 both directions of the IRE trade boundary shared one
`Imports & trade` label, so the Sankey drew ribbons running **into** a node that
reads as a source. Traced on 2022 the inflow is entirely legitimate exports:

| Flow (2022) | PJ | What it is |
|---|---:|---|
| `IBOBIOPEL*` → `EXPBIOPEL` (`BIOPEL`) | 10.94 | wood pellets produced in Wallonia and exported |
| `EXPBIOLOG` (`BIOLOG`) | 1.43 | wood logs exported |
| Power plants → `Transfo_Exp` (`ELCHIG`) | 28.71 | HV electricity exported (`Transfo_Exp` → `ELCEXP` → `EXPELCHIG`) |
| `IMPELCHIG` / `IMPELCOFFWINBE` → `Transfo_Imp` (`ELCIMP`) | 0 in 2022 (10.6 in 2030, 36.0 in 2050) | **import adaptor**, both ends inside the node → self-loop, dropped by the collapse |

`EXPBIOCPS`/`EXPBIOLOG`/`EXPBIOPEL`/`EXPELCHIG`/`Transfo_Exp` therefore carry
`custom = sankey_overview = Exports`, and `infer_overview_process_label` returns
`Exports` for `EXP*` codes. Aggregation Level 2 already distinguished the two
(`Imports` vs `Export biomass` / `Electricity Exports`), so nothing about the
demand extraction changes — this is a display split only. `sankey_overview` now
has 12 process labels + 8 commodity labels = 20, on the ≤20 budget (the three
`ERNW_PV-*` rows and `COMSOL00`/`RSDSOL00` moved from `PV` to `Power plants` at
overview level, since `custom` is the level that splits generation and gets `PV`
from `_generation_split_label` anyway).

Commodity context hubs never mix fundamentally different carriers (e.g. gas vs electricity; uranium stays **Nuclear fuel**, not Electricity).

### Building retrofits (labelling, not dropping)

TIMES structure (understood):

1. `Dum_Retrofit` / `Dum_Retrofit_Commercial` (`.IMP.IRE.`) create dummy MM2 option commodities `Dum-Retrofit-*` — capacity/availability accounting. They have **no** `pypsa_carrier`, so they never enter the energy Sankey (same universal carrier filter as any unmapped non-energy commodity). They are labelled **Building retrofits**, not Imports & trade.
2. `Retrofit-*` processes take those dummies (+ `NRGI`) and **produce useful-heat commodities** (`RH*`, `CH*`) — the same hubs that boilers feed. Soft-link rule `retro` tags `VAR_FOut` on `Retrofitting improvements`.
3. On the energy Sankey those heat FOuts therefore appear with **no energy FIn** (dummies were non-carrier). That can look like a primary source; it is demand-side efficiency accounting in TIMES, not a fuel import.

**Do not drop these flows.** Soft-linking is universal: every matched export flow stays in QA views. If the left-side placement remains confusing, improve labels/layout — never hide the soft-link. Whether PyPSA should receive retrofit useful-heat FOut as heat supply, a demand reduction, or a separate efficiency signal is an open coupling-design point (see [Open points](#open-points-and-points-of-attention)); the extractor must still show what TIMES reports.

### Underlying export status → colour

Each collapsed link keeps an `export_status` (`exported` / `context`; `mixed` and
`double_count` are defined but do **not** occur on real `custom` data). At display
time an `exported` link is coloured by its `export_sector`
([Export strategy](#export-strategy-per-sector-colours--demand-inflow-anchoring));
`context` is grey. `export_status` is part of the collapse key, so a node pair
carrying both kinds of energy yields one coloured and one grey ribbon rather than
a single promoted one. Coloured PJ is **path energy** after collapse/netting, not
a 1:1 sum of tagged export rows — some tagged mass ends in magenta `U ·` sinks
(FOut-only DEM / hub imbalance) rather than a process→process ribbon, so the
coloured share can sit below 100% of tagged export PJ, but it can no longer sit
above it.

Edit `custom` freely in `data/mapping_processes.csv` and
`data/mapping_commodities.csv`, then re-run QA with `--agg-level custom`; use
`qa_sankey_label_map_{year}.csv` to see which TIMES codes sit under each label.
**Editing `custom` does not change the PyPSA demand CSVs** — extraction filters on
Aggregation Level 2 / `process_agg` + `extraction_rules.csv`, independent of the
display level.

---

## Designing `sankey_overview` (worked example)

Goal: a whole-system Sankey with **at most ~20 nodes**, still reflecting the main energy story (supply → conversion → end use).

**Process side (9 labels)** — cluster by role, not by TIMES sector alone:

| Label | Criterion |
|-------|-----------|
| Imports & trade | `Sector=IMP` or Aggregation Level 2 contains import/export |
| Power plants | Electricity sector generation (not fuel-tech PRE) |
| CHP | `Type=CHP` or CHP Aggregation Level 2 |
| District heating | District heating / heat-exchanger labels |
| Fuel supply / Fuel conversion | `Sector=SUP` (overview uses **Fuel conversion** for SUP synthesis + end-use SUP fuel techs) |
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

## Practical tips

- Edit the mappings under `data/` (single source of truth for TIMES_PyPSA and pypsa-wal).
- Do **not** invent Sankey display prefixes in code (`Unknown`, `Unmapped: …`); put the intended label in the CSV, or rely on TIMES Description/code fallbacks.
- Full TIMES dictionaries for names: `data/AllProcesses.csv` and `data/AllCommodities.csv` (semicolon-separated VEDA exports). The loader uses these to fill missing process/commodity descriptions on flows.
- Extraction rules filter on Aggregation Level 2 / `process_agg`. Changing `sankey_overview` or `custom` does not change PyPSA demand exports — changing **Aggregation Level 2** does.
- Legacy aliases: `L0`→`Sector`, `mapping`→`Aggregation Level 2`, `L2`→raw codes.

## Mapping Sankey nodes back to TIMES codes

When debugging a Sankey node, use the QA crosswalk CSV (written next to the HTML report):

- **`qa_sankey_label_map_{year}.csv`** — for each aggregated Sankey label, lists the original TIMES `times_code` / `times_description` members (process or commodity), the raw mapping CSV label (`mapping_label`, empty when inferred), and the contributing energy (`value` in the selected unit).

Related flow-level files (same `out-dir`):

- **`qa_flows_{year}.csv`** — annual VAR_FIn/VAR_FOut rows with original `process_code` / `commodity_code` / descriptions, export tags, and carriers (before Sankey collapse).
- **`qa_export_neighborhood_{year}.csv`** — neighbourhood subset used for export-focused Sankeys (still at TIMES code resolution).

Example: to see which TIMES processes sit under `Buildings` after `--agg-level sankey_overview`:

```bash
python -c "import pandas as pd; m=pd.read_csv('output/qa_overview/qa_sankey_label_map_2030.csv'); print(m.query(\"side=='process' and sankey_label=='Buildings'\")[['times_code','times_description','TWh']].head(20))"
```

## Export neighbourhood (n−1 / n / n+1)

1. Core = process and commodity codes on flows matched by extraction rules
2. Keep every energy flow that shares a core process **or** core commodity
3. That includes upstream producers / other inputs (n−1) and downstream consumers / other outputs (n+1)
4. Aggregate with the selected `--agg-level` labels; colour exported links by PyPSA sector, context grey

---

## Coverage gap: what stays out of the export (and why)

The DMD coverage gap (`qa_coverage_gap_*.csv`) is **324.7 PJ (2030) / 298.0 PJ
(2050)** — measured on the current rules after `services other fuel` was added
(325.6 / 298.8 PJ before it). The "356 / 331 PJ" quoted earlier on 2026-07-25 was
itself taken mid-audit, before the later rule fixes of that day. The split is
**not** "almost all consumption-side":

| Class | 2030 | 2050 |
|-------|-----:|-----:|
| (a) downstream (n+1/n+2) of already-exported production — correct to exclude | ~239 PJ (**74%**) | ~209 PJ (**70%**) |
| (c) non-energy / material accounting **or commodity missing from `mapping_commodities.csv`** | 68.3 PJ (**21%**) | 73.8 PJ (**25%**) |
| (b) PJ commodity with no exported producer | ~17 PJ (5%) | ~15 PJ (5%) |

Class (c) is measured directly (rows with no `pypsa_carrier`); class (b) is the
earlier count and class (a) is the remainder, so those two are approximate.

Class (c) is **not all non-energy**: it includes ~37 PJ (2030) / ~42 PJ (2050) of
industrial heat / mechanical-energy commodities (`ICHHTH` 11.3, `ICHMCH` 8.4,
`IOIHTH` 4.8, `ICHPRC`, `IOIMCH`, …) that are simply **absent from
`mapping_commodities.csv`** — unclassifiable rather than genuinely non-energy.
307 (2030) / 317 (2050) commodities appearing in flows have no mapping row at all.
Truly unattributable residue is <1 PJ/yr.

Class (a) — legitimately excluded because exporting both ends would double-count:

| Unexported flow | Why excluding is correct |
|-----------------|--------------------------|
| `Buildings: built area` × Heat (~78 PJ) | Buildings consume the boiler heat already exported as the BEWAL / boiler `VAR_FOut`. |
| `Cars` / `Road Freight` × `total energy for transport` | Vehicles consume the transport fuel `total road` already exports at the fuel-tech `VAR_FIn`. |
| `Industry` / `IND_DMD_MT_*` × gas / electricity / heat / coke for industry | Consumption of fuels the industry fuel-tech rules export one hop upstream. |
| `Building Existing *` × Commercial lighting / cooling / Heat | Service consumption downstream of `commercial other` electricity (captured) and the services-heat rules. |
| Commercial / residential heat-pump & electric-heater electricity | Captured via their **heat `VAR_FOut`** (BEWAL rules), not their electricity `VAR_FIn`. |

## Open points and points of attention

Genuine open items; the debugging history that produced the current mappings is
in the change log below.

### Audit 2026-07-25 — what was fixed, and what is still open

The audit found the defects below. Those marked **fixed** were applied on
2026-07-25; the demand CSVs changed only where a mapping label was wrong (7 of 55
categories, all in services heat and `retro`). The rules-schema rewrite itself is
value-neutral: all 46 emitted PyPSA input files are byte-identical.

**Fixed**

- **`filter_values_2` was silently ignored for 9 of 55 rules.** The old
  `load_extraction_rules` read only `filter_values_1` unless
  `filter_type == "combined"`, so 9 rules declared a `pypsa_carrier` filter that
  never ran. Honouring them as written would have zeroed `solid biomass`,
  `naphtha` and `coke`, because the declared carriers described what each tech
  *emits* while `var_type=VAR_FIn` measures what it *consumes*. The CSV was
  therefore rewritten to state the carriers actually matched, and the loader now
  applies every populated cell. Only `hydrogen` (a `VAR_FOut` rule) had a
  genuinely redundant filter. `total agriculture machinery` also carried a dead
  `Fuel Tech – Diesel` (en dash, a process label in a carrier column) — removed.
- **A typo'd filter field exported the whole system.** `_apply_extraction_rule`
  had no `else` branch, so `proces_agg` applied *no* filter. It now raises.
- **Emissions could be summed as energy.** Nothing excluded CO₂; only the
  incidental `pypsa_carrier=Heat` filters kept it out. Dropping that filter from
  `residential urban decentral gas boiler` turns 16.9 PJ into **2000.8**
  (`NETSCO2N` + `RSDCO2N` ≈ 975 kt each), and `retro` — which has no carrier
  filter — was protected by luck alone. `drop_emission_commodities` now runs for
  every rule.
- **Commercial new electric heating was mislabelled.** 16 `Com Space/Water Heat
  New Boiler … ELC101/ELC201` processes carried `commercial other` (14) or
  `Commercial Biomass boiler` (2) while the *existing* vintage `…ELC100` carried
  `Commercial electrical stove` — the same technology under two labels. All 16
  now use `Commercial electrical stove`. 2050: `services electric heater`
  +6.32 PJ, `BEWAL services urban decentral heat` +6.04 PJ, `total electricity
  services` −6.15 PJ, and 2670 MW of commercial electric heating capacity appears
  in `heating_capacities_2050.csv` for the first time. Three solar processes
  (`CWNCOSOL101`, `CWNCSSOL101`, `CWNENSOL101`, all consuming `COMSOL`) were
  labelled gas or hot water → `Commercial solar thermal`.
- **6 commercial retrofits were labelled `Dum_Retrofit_Commercial`** (a
  code-as-label fallback) while their 47 siblings had `Retrofitting improvements`,
  so `retro` undercounted by 1.59 PJ in 2050. Relabelled. The two `Dum_Retrofit*`
  *dummy* processes keep their own labels — they carry year-invariant non-energy
  values (599 / 241) and must stay out of the rule.
- **`hydrogen road` was declared a child of `total road`.** The key overlap is 0
  in every year: `total road` tags the electricity at `Fuel Tech - H2`,
  `hydrogen road` tags the H₂ at the vehicles. The parent link is removed, so the
  double-count matrix no longer excuses a relation that is energetic (n+1), not a
  subset. **Summing the two on the PyPSA side still double-counts** 0.48 PJ (2050).
- **`coal` was on the known-zero allowlist** but is non-zero throughout
  (6.80 PJ in 2030 after the `INDCOA00`/`INDCOK00` label unswap). `expect=nonzero`.
- **Unmapped commodities inside exported categories are now explicit.** `BIOSLU`,
  `MBORES`, `MBOWOO` (`solid biomass`) and `AUX_STH2SGT` (`total road`) have no
  row in `mapping_commodities.csv`; they are listed in the rule's
  `commodity_code` column instead of arriving invisibly.
- **`_adjust_road_rail_totals` used unguarded `.iloc[0]`** on four categories →
  `IndexError` if any were removed from the CSV. It now skips and warns.
- **`total domestic navigation` exported a Btkm activity value as PJ.** `TNDF` is
  produced by `TNDFDST00`, Activity unit **BTKM**: 1.617 against 0.572 PJ of
  diesel actually burnt → 283% implied efficiency. The rule now reads the ships'
  fuel (`VAR_FIn`, carrier `total energy for transport`) → **0.572 PJ (2030) /
  0.672 PJ (2050)**, and the same diesel is subtracted from `total road`, which
  measures it at the fuel tech. `exported_unit_check` is now empty in all 8 years.
  The carrier filter also covers `TRABDL`, so the currently-unbuilt
  `TNAINLBDLN01` biodiesel ship is picked up if it is ever deployed.
- **The `adjust` column is now applied.** The road−rail subtractions were
  hardcoded in `_adjust_road_rail_totals` while the CSV declared them; the
  corrections are now driven by `adjust` (`subtract:<category>`,
  `subtract_internal_transfer`), so adding the navigation subtraction was a data
  edit rather than a code change.

**Resolved 2026-07-25 (second pass)**

- **Black liquor: excluded on purpose, and the `MT` unit was wrong.** `INDBLQ` is
  `.NRG.` / **PJ** in `data/AllCommodities.csv`; the 2026-07-25 audit had set it
  to `MT` in `mapping_commodities.csv` (confusing it with the `.MAT.` `MBORES`).
  Corrected, and `test_mapping_units_match_the_veda_dictionary` now compares
  every mapped commodity's `Unit` against the VEDA export (`INDBLQ` was the only
  disagreement in the whole file).

  The flow is a closed loop inside the pulp mill:

  | Direction | Process | 2030 | 2050 |
  |---|---|---:|---:|
  | produced | `IPPPUPCHE00` / `IPPPUPCHE01` (chemical pulp production) | 8.49 | 10.20 |
  | consumed | `CHPINDBLQIPPN00_N` (pulp & paper autoproducer CHP) | 8.49 | 6.21 |
  | consumed | `BBLQH2G110` (black-liquor gasification → H₂) | 0 | 3.99 |

  Black liquor never passes through a `Fuel Tech … (IND)` gateway — the pattern
  every industry rule measures at — because it is never bought. Exporting it as
  `solid biomass` would make PyPSA source 6–10 PJ from the BEWAL/ENSPRESO wood
  potential, displacing real forestry biomass for a residue that has no market.
  The same argument covers the 1.55 PJ of `INDCPS` the mills produce themselves
  (distinct from the 16.0 PJ that *does* come through `Fuel Tech - Wood CHIPS
  (IND)` and *is* exported). The exclusion is now stated in the `solid biomass`
  rule's `note`. Revisit only if PyPSA gains a pulp-mill by-product supply.

- **`electricity road`: the two "measurement points" are two different vehicle
  fleets.** Tracing the commodities shows two parallel chains, not one energy
  counted twice:

  | Chain | 2050 PJ | Consumers |
  |---|---:|---|
  | `EVTRANS_M-L` → `ELCLOW` → `TRAELC00` (`Fuel Tech - Electricity (TRA)`) → `TRAELC` | 36.95 | electric heavy trucks 34.56, rail 2.01, two/three-wheelers 0.38 |
  | `RSDELC`/`COMELC` → `TCHARGHOMN01`/`TCHARGWRKN01` (`EV charger`) → `BATELCIN` → `TSTGEVCMIDN01` (`TRA_STG_PJ_GW`) → `BATELCOUT` → electric cars | 25.02 in / 23.77 out / 23.30 to cars | car fleet only |

  The defect was therefore not "mixed points" but an **inconsistent metering
  depth**: the truck/rail leg is read at the low-voltage grid, the car leg was
  read one step *past* the charger, so the 1.25 PJ charger loss (5%) sat in no
  category. Both rules (`electricity road` and its parent `total road`) now list
  `EV charger` instead of `TRA_STG_PJ_GW`, so both legs are metered at the
  low-voltage grid — which is what PyPSA needs, since it models the electricity
  bus and applies its own charger/battery efficiencies downstream. No new
  double-count: the chargers are `Aggregation Level 2 = EV charger`, so their
  `RSDELC`/`COMELC` draw is outside `residential other` / `commercial other` and
  outside `total electricity residential` / `services`. The remaining 0.48 PJ EV
  battery round-trip loss stays downstream of the metering point, as PyPSA models
  the BEV battery itself.

  Effect: `electricity road` and `total road` each **+0.348 TWh (2050)**, +0.072
  (2030); no other category moves. The Sankey highlight for this leg now sits on
  `Residential electricity → EV chargers` / `Electricity → EV chargers`
  (`EV chargers` is not an anchorable gateway, so it is not moved downstream).

- **`residential cooking`: two mislabelled stoves and one industrial process.**
  The `RCOK*` family is one technology family with one vintage suffix, but two
  members had drifted out of the label:

  | Process | Description | old L2 | new L2 |
  |---|---|---|---|
  | `RCOKCOA100` | `Rsd.Cooking.COA.00.Stove.` | `residential other` | `residential cooking` |
  | `RCOKELC100` | `Rsd.Cooking.ELC.00.Stove.` | `residential other` | `residential cooking` |
  | `RCOKGMX100` / `RCOKLOG100` / `RCOKLPG100` | `Rsd.Cooking.{GAS,LOG,LPG}.00.Stove.` | `residential cooking` | unchanged |
  | `RCOKELC101` / `RCOKGAS101` | cooking electric / gas stove | `residential cooking` | unchanged |
  | `INFPRCCOK01` | `IND.Other Non Ferrous Metals.Process Heat.COKE.01` (outputs `INFPRC`) | `residential cooking` | **`Industry`** |

  This is the same defect class as the 16 commercial electric heaters: the same
  technology under two labels depending on vintage. `INFPRCCOK01` is an
  industrial process-heat tech that landed in a residential category on a
  `COK`/cooking name clash; its siblings `INFPRCCOA01` / `INFPRCGMX00` were
  already `Industry`.

  **The mixed-carrier total stays mixed** — PyPSA takes one cooking demand — but
  the electric part is now also emitted as the child category
  **`residential cooking electricity`** (0.62 TWh 2021 → 0.43 TWh 2050). That
  matters because `prepare_sector_network.py` in pypsa-wal scales the Walloon
  electricity load on `total electricity residential + total electricity services
  + total rail`, and `RCOKELC100`'s electricity has now left that total; the sum
  on the PyPSA side must gain `residential cooking electricity` or the Walloon
  electricity load drops by ~0.6 TWh. Nothing in pypsa-wal reads
  `residential cooking` itself today.

  Effect: `residential cooking` +0.62 TWh (2021), +0.25 (2030), +0.04 (2035);
  `total electricity residential` the mirror image; the `Coke for industry`
  carrier filter (only ever there for `INFPRCCOK01`) is replaced by
  `Coal for residential sector`.

- **`coal` / `coke`: the mapping labels were swapped, not the category names.**
  The TIMES `Description` on the same mapping row contradicted the label:

  | Process | TIMES Description | consumes | produces | old L2 | new L2 |
  |---|---|---|---|---|---|
  | `INDCOA00` | `Fuel Tech - Hard Coal (IND)` | `COAHAR` Hard Coal | `INDCOA` | `Fuel Tech - Coke (IND)` | `Fuel Tech - Hard Coal (IND)` |
  | `INDCOK00` | `Fuel Tech - Coke (IND)` | `COACOK` Coke | `INDCOK` | `Fuel Tech - Hard Coal (IND)` | `Fuel Tech - Coke (IND)` |

  Unswapping them makes both categories read what their names say — `coal` = hard
  coal + lignite (PyPSA-Eur's "solid fossil fuel for industry"), `coke` = coke —
  and fixes the phase-out story: `coke` ends in 2035 while hard coal persists to
  2050, the opposite of what the swapped labels reported. Values swap between the
  two categories (2050: `coal` 0 → 1.854 TWh, `coke` 1.854 → 0).

- **`naphtha` is *not* misnamed.** `naphtha` is PyPSA-Eur's name for the
  industrial **oil** bus (`spatial.oil.naphtha` / carrier `naphtha for industry`
  in `prepare_sector_network.py`), covering energy *and* feedstock oil.
  pypsa-wal's own non-TIMES branch fills the same column with
  `Non-energy consumption of oil for the feedstock production + Total Final oil
  consumption in industry` — which is precisely what the rule reads (`NEC`/`NEO`
  oil feedstock + `INDHFO00` + `INDLFO00`). The name follows PyPSA, the contents
  follow the name's PyPSA meaning; nothing to fix.

  What *was* wrong is that the `Non-energy` process also consumes **gas and
  coal**, and the rule's carrier list let both into the oil bus: 8.28 PJ of
  `GASNAT` chemical feedstock (2021–2025) and 0.033 PJ of `COAHAR`. Those now go
  to `methane` and `coal`, which is where PyPSA sources them. `naphtha` −2.309 TWh
  / `methane` +2.300 TWh, 2021–2025 only.

**`electricity` really did count industrial solar twice** (fixed 2026-07-25).
`INDSOL` and `RENSOL` are not two names for one flow, they are two consecutive
links of one chain: `MINRENSOL` (Solar Potential) → `RENSOL` → **`INDSOL00`**
(`Fuel Tech - Solar (IND)`, a 1:1 adaptor) → `INDSOL` → **`INDPVELC`**
(`PV industrial`, also 1:1) → `INDELC`. The rule listed *both* the adaptor's
`VAR_FIn` and the PV process's `VAR_FIn`, so the same on-site PV was added to the
industrial electricity demand twice — 0.4496 PJ in 2021, 0.7994 PJ in 2025–2035,
and nothing from 2040 (the process disappears). Because the whole chain has
efficiency exactly 1.000, the duplicate was invisible in every ratio check.
`Fuel Tech - Solar (IND)` and carrier `Renewable: Solar` are dropped; the rule
keeps `PV industrial`, the leg where the PV electricity actually enters `INDELC`,
consistent with the `measure_at = fuel_input` convention. `electricity` −0.222 TWh
(2025–2035), −0.125 TWh (2021). The resource side is *not* double counted:
`MINRENSOL` produces 26.70 PJ in 2030 and the four sector fuel techs (ELC 18.69,
RSD 5.41, COM 1.80, IND 0.80) consume it exactly.

**Commercial electricity: one total is right, but data centres deserved their own
line** (resolved 2026-07-25). `total electricity services` reads
`process_agg = commercial other`, carrier `Electricity`, at `demand_input` — 92
processes, **29.31 PJ in 2030 / 50.64 PJ in 2050**. It correctly *excludes*
commercial heat pumps and electric stoves, which PyPSA supplies from its heat bus
with its own COP. Composition:

| End-use (process prefix) | 2030 | 2050 |
|---|---|---|
| Data centres / servers (`COSE*`) | 9.37 PJ — **32.0%** | 24.73 PJ — **48.8%** |
| Other equipment / appliances (`COEL*`, `COENMIX*` elec.) | 8.22 PJ | 9.36 PJ |
| Lighting, buildings + public (`CLIG*`, `CPLI*`) | 6.55 PJ | 6.54 PJ |
| Space cooling (`CC*`) | 4.09 PJ | 8.89 PJ |
| Refrigeration (`CREF*`) | 1.07 PJ | 1.12 PJ |
| Electric cooking (`CCOK*ELC*`) | 0.01 PJ | 0.001 PJ |

No further split is offered, and that is deliberate: pypsa-wal substitutes a
single `total electricity services` scalar into the Walloon electricity load
(`prepare_sector_network.py`, alongside `total electricity residential` and
`total rail`), and PyPSA-Eur has no cooling / lighting / appliance bus to receive
the parts. A split also cannot be *expressed* with the current rule columns —
all 92 processes share one `Aggregation Level 2` label and one carrier, so
children need new labels, not new filters. Data centres got that new label
(`Commercial data centres` on `COSEELC100`/`COSEELC101`) because they nearly halve
the meaning of the total between 2030 and 2050; the child category
**`services data centre electricity`** (0.69 → 2.60 → 6.87 TWh) exposes it while
the parent stays byte-identical. Being a child, it must **not** be added to any
pypsa-wal sum.

### Which demand keys pypsa-wal actually reads

Before adding a category it is worth knowing whether anything on the PyPSA side
will read it. `build_population_weighted_energy_totals.py` substitutes on
**column-name match** (`nodal_totals.columns.intersection(wallon_demands.index)`)
and `build_industrial_energy_demand_per_node.py` does the same against the
industry frame — so a category lands in a PyPSA input file as soon as its name
matches an existing column. That is *not* the same as being used: several
`build_energy_totals.py` columns are written and then read by nobody.

Audited 2026-07-25 across `scripts/` and `rules/` in pypsa-wal:

| Key | Where it is consumed |
|---|---|
| `total {residential,services} {space,water}`, `electricity {…} {…}` | `build_heat_demand` → the heat loads (`uses = ["water", "space"]` only) |
| `distributed heat {residential,services}`, `thermal uses {…}` | `build_district_heat_share`, sufficiency heat rescaling |
| `total electricity residential` + `total electricity services` + `total rail` | the single factor that scales the whole Walloon electricity load |
| `total {domestic,international} aviation`, `total domestic navigation` | `add_aviation`, `add_shipping` loads |
| `total agriculture {electricity,heat,machinery}` | `add_agriculture` loads |
| `BEWAL {residential urban decentral,residential rural,services urban decentral} heat`, `{residential,services} district heating` | `write_wallon_heat_demands` rescales the Walloon heat loads |
| industry columns (`electricity`, `coal`, `coke`, `methane`, `hydrogen`, `naphtha`, `solid biomass`, `low-temperature heat`, `ammonia`, `methanol`) | `add_industry` loads |
| **`total services cooking`, `electricity services cooking`, `total residential cooking`, `electricity residential cooking`** | **nothing** |
| **`total services`, `electricity services`\*, `total residential`** | **nothing** (\*`electricity services` only in the unrelated sufficiency branch) |

The cooking rows are the load-bearing finding: PyPSA-Eur builds tertiary and
residential heat from `space` + `water` only (`build_hourly_heat_demand.py`:
`uses = ["water", "space"]`), and cooking is dropped for every country. So
writing `total services cooking` from TIMES would have produced a column in
`pop_weighted_energy_totals.csv` that no PyPSA component ever looks at — a
coverage table that reads 100% while the energy is still missing from the model.

### Tertiary non-electric fuel: `services other fuel`, and why `total services cooking` is a dead end

The 0.87 PJ is four processes, and only these four of the ~92 `commercial other`
processes consume anything but electricity — so a carrier filter isolates them
exactly:

| Process | Description | 2030 | 2050 |
|---|---|---:|---:|
| `COENMIX100` | `Com.Other Energy.00.Other.` | 0.802 | 0.802 |
| `COENMIX101` | `Com.Other Energy.101.Other` | 0.043 | 0.034 |
| `CCOKGMX101` | `Com.Cooking.GMX.01.Stove.` | 0.020 | 0.037 |
| `CCOKLPG101` | `Com.Cooking.LPG.01.Stove.` | 0.002 | — |
| **total** | | **0.867 PJ** | **0.873 PJ** |

By carrier in 2030: network gas 0.568, oil 0.262, LPG 0.017, wood chips 0.010,
gasoline 0.005, pellets 0.004, biodiesel 0.001 PJ. Flat across the horizon
(0.802 PJ in 2021 → 0.910 in 2045 → 0.873 in 2050). `Com.Other Energy` is a 1:1
pass-through in TIMES (`VAR_FOut COEN` = Σ`VAR_FIn`, efficiency 1.000); the two
cooking stoves are 61% efficient, so 0.008 PJ of the total is stove loss rather
than delivered service.

The new rule reads it at `demand_input` on `process_agg = commercial other` with
the seven non-electric commercial carriers. It is **disjoint** from
`total electricity services` (carrier `Electricity` only) and from the services
heat rules (different Aggregation Level 2), so it is a sibling total with no
parent, and `qa_double_count_*.csv` stays empty. Services still reconciles
exactly on the Sankey (`coloured_share` = 1.000 in 2030 and 2050) with the new
flows coloured teal.

**Why not `total services cooking`.** It is a real `build_energy_totals.py`
column, so the substitution would have worked mechanically — and delivered
nothing, because no PyPSA component reads it (table above). It is also the wrong
name: 0.80 of the 0.87 PJ is `Com.Other Energy`, not cooking.

**What pypsa-wal must do to serve it.** The only live tertiary non-electric sink
is the services heat load, rescaled in `write_wallon_heat_demands`:

```python
heat_categories = [
    "BEWAL residential urban decentral heat",
    "BEWAL residential rural heat",
    "BEWAL services urban decentral heat",
]
for heat_demand in heat_categories:
    target_heat = wallon_heat.loc[[heat_demand], "TWh"].sum()
```

`services other fuel` must be **added** to the services target
(`wallon_heat.loc[["BEWAL services urban decentral heat", "services other fuel"], "TWh"].sum()`),
exactly as `residential cooking electricity` has to be added to the
electricity-load sum. That is +0.24 TWh, i.e. **+5.7%** on the 4.21 TWh
(15.17 PJ) Walloon services decentral heat load in 2050, and it brings ~50 kt/yr
of CO₂ (`COMCO2N` 48.6 kt on `COENMIX100` alone) inside the modelled system
instead of leaving it out of the Walloon balance.

Two caveats to agree before wiring it, both stated so the choice is explicit:

- **PyPSA-Eur drops this bucket for every other node.** Its own tertiary
  accounting keeps space + water heat and electricity and discards
  cooking / other-energy, so serving Wallonia's 0.87 PJ makes the Walloon node
  more complete than its neighbours rather than consistent with them.
- **The heat bus is an approximation for these end-uses.** On the heat bus the
  fuel becomes a demand a heat pump may serve at COP 3, which is a reasonable
  electrification story for cooking and miscellaneous building energy but not for
  the 0.005 PJ of commercial gasoline. The alternative — a dedicated inelastic
  Load on the gas / oil buses, as pypsa-eur does for `gas for industry` and
  `agriculture machinery oil` — preserves the fuel and its CO₂ exactly but needs
  new components in `prepare_sector_network.py` rather than one changed line.

Until that line changes, `services other fuel` is emitted and visible (it left
`qa_coverage_gap_*.csv`, which drops from 325.6 to 324.7 PJ in 2030) but not yet
served by PyPSA.

**Resolved 2026-07-25 (third pass — the pypsa side was read)**

- **The commercial fuel is now soft-linked, as `services other fuel`.** The
  0.87 PJ that no rule matched is the non-electric legs of `Com.Other Energy`
  (`COENMIX100`/`COENMIX101`) plus the gas and LPG commercial cooking stoves
  (`CCOKGMX101`, `CCOKLPG101`). It is now a category of its own; see
  [§ Tertiary fuel](#tertiary-non-electric-fuel-services-other-fuel-and-why-total-services-cooking-is-a-dead-end)
  for the composition, why `total services cooking` was the wrong route, and the
  one-line change pypsa-wal needs to serve it.
- **`total electricity services` will not be split further.** PyPSA-Eur has no
  bus for the parts — verified, not assumed: `cooling` appears nowhere in
  `prepare_sector_network.py` except the EV-cabin temperature correction, and
  there is no lighting, appliance or refrigeration carrier anywhere in the
  Walloon build. `total electricity services` is consumed as a **single scalar**
  that scales the whole Walloon electricity load
  (`prepare_sector_network.py`, alongside `total electricity residential` and
  `total rail`). Data centres stay the one informational child, because they go
  from 32% of the total in 2030 to 49% in 2050 and a user may want to site or
  shape them; cooling, lighting, appliances and refrigeration would be numbers
  with nowhere to go. This point is closed as a **no**, not left open.

**Verified correct (audit closed these):**

- **Rail electricity is tagged twice with different flow keys** — by `total road`
  at the fuel tech and by `total rail` + `electricity rail` at the rail process
  (2.01 PJ, 2050) — but `_adjust_road_rail_totals` corrects it by subtraction, so
  the exported numbers are right. The open part is only *visibility*:
  `qa_double_count_*.csv` keys on the flow, so it cannot show this pair.

- **`road_internal_transfer_pj` is exact.** 2030: 4.9911 PJ, all `TRABDL`
  (produced 5.1773 by the biodiesel fuel tech, of which 4.9911 consumed by the
  diesel fuel tech inside the road set + 0.1863 direct-to-bus outside — balance
  exact). 2050: 0.3414 PJ. Neither over- nor under-subtracts. Latent risk only:
  `min(net_prod_inside, net_cons_inside)` would over-subtract on a scenario where
  a blended biofuel is *partly imported*.
- **Aviation FOut really is PJ.** `TAIFKER00` FIn `TRAKER` 26.6093 → FOut `TAIF`
  26.6093 — efficiency exactly 1.000, all three processes have Activity unit PJ,
  and the sum 30.3329 equals the category total to 6 decimals. `TRAKER` is
  produced only by a fuel tech in no rule and consumed only by the aviation
  processes, so the ~30 PJ is counted exactly once. Both questions are now closed:
  the rules read the kerosene `VAR_FIn` (see above), which is what PyPSA loads.
- **No disallowed double-count overlaps** in either year; `ALLOWED_OVERLAPS` is
  complete for the current rule set, and the two rules added in 038a419/224b429
  introduce none. (`"total electricity residential": frozenset()` is a no-op.)
- **`total road` inflation by ship diesel is real but negligible** — 0.57 PJ,
  0.59% of the category.
- **EV charging** has no double count: the charger input (25.0 PJ, 2050) is now
  read by `electricity road` / `total road` (see above) and `total electricity
  services` / `residential` correctly exclude it, because the chargers are
  `Aggregation Level 2 = EV charger`, not `commercial other` / `residential other`.

### Extraction rules / demand CSVs

- **Aviation reads the jet fuel** (resolved 2026-07-25). `add_aviation` in
  pypsa-wal's `prepare_sector_network.py` sums `total international aviation +
  total domestic aviation` into a `kerosene for aviation` **Load** fed from the oil
  bus, so PyPSA expects the fuel, not the service. Both rules now use
  `measure_at=demand_input` / `VAR_FIN` on `Kerosene - Jet Fuels for transport`
  (`TADFKER00`, `TADPKER00`, `TAIFKER00`, `TAIPKER00` and, from 2035, the
  `TAVDOM*` successors — all of which also consume `TRAKER`). The exported values
  are **unchanged**, because TIMES gives every aviation process efficiency exactly
  1.000, so `TADF`/`TADP`/`TAIF`/`TAIP` numerically equalled the kerosene; reading
  the fuel removes the risk of silently exporting a service if that ever stops
  holding. `TRAKER` is produced only by a fuel tech in no rule, so there is still
  no double count, and no `VAR_FOut` rule remains outside heating.
- **`total international navigation` is always 0.** Its filter label
  `international navigation` is absent from `mapping_processes.csv`
  Aggregation Level 2, so the rule matches nothing; the category is on the
  known-zero allowlist. If international navigation should be non-zero, correct
  the label.
- **Known-zero allowlist** (`expect=zero`): `ammonia`, `methanol`,
  `total international navigation`. Re-verified 2026-07-25 — see
  [§ ammonia and methanol](#ammonia-and-methanol-re-verified-2026-07-25), because
  the previous justification ("no ammonia commodity exists in the model") was
  wrong for `ammonia`. `coal` was wrongly on this list; it is 6.80 PJ in 2030
  and non-zero in every year. Several residential coal / biomass / solar / oil
  boiler categories, and `coke` from 2035, are 0 late but non-zero earlier —
  expected.

#### `ammonia` and `methanol`, re-verified 2026-07-25

Both stay zero, but only one of them is genuinely absent from TIMES-WAL.

**`methanol` is structurally absent.** No commodity and no process anywhere
matches methanol or MeOH — not in `AllCommodities.csv`, not in
`AllProcesses.csv`, not in the `.vd`. Wallonia's chemistry is aggregated into
`ICH` (Other Chemicals Demand, PJ) and the two non-energy buckets `NEC`
(Chemicals) / `NEO` (Others), whose fuels the `methane` / `naphtha` / `coal`
rules already read. There is no methanol product to report. PyPSA-Eur's
`methanol` industry key (`MWh_MeOH_per_tMeOH` on its `Methanol` sub-sector) has
no TIMES counterpart, and exporting 0 correctly zeroes it for the Walloon node.

**`ammonia` exists — the old note was wrong — but zero is still right.**
TIMES-WAL has `IAM` (`Ammonia Demand`, **Mt**, `.DEM.`, IND), met by
`IAMSTDPRO00` and `IAMSTDPRO01`:

| | 2021 | 2022 | 2025 | 2030 → 2050 |
|---|---:|---:|---:|---:|
| `IAM` produced (Mt) | 0.319 | 0.319 | 0.319 | **0** |
| `INDGAS` consumed (PJ) | 3.482 | 3.562 | 3.804 | 0 |
| `INDELC` consumed (PJ) | 0.096 | 0.140 | 0.271 | 0 |
| process + combustion CO₂ (kt) | 655 | 655 | 674 | 0 |

Three independent reasons the export stays 0, any one of which is sufficient:

1. **The scenario retires it.** The exogenous demand (`EQ_Combal` on `IAM`) is
   declared for 2021, 2022 and 2025 only, and there is no `VAR_Act` on any `IAM`
   process from 2030 on — capacity idles down from 0.223 Mt (2030) to 0.011 Mt
   (2050) without producing. Every coupled horizon is ≥ 2030, so zero is the
   scenario's own answer, and it is arguably a *correction*: pypsa-eur derives
   its `ammonia` column from historical `ammonia_production.csv`, which would
   keep a closed Walloon plant alive.
2. **The energy is already soft-linked.** The plant buys `INDGAS` from
   `INDGAS00` (`Fuel Tech - Natural Gas transport (IND)`) and `INDELC` from
   `INDELC00` (`Fuel Tech - Electricity (IND)`) — both inside the `methane` and
   `electricity` rules. PyPSA-Eur goes the other way: when `sector: ammonia` is
   on, `build_industry_sector_ratios.py` *subtracts* the ammonia SMR gas and
   electricity from industrial methane/elec before loading `ammonia` as an NH₃
   product demand. A non-zero `ammonia` on top of the TIMES `methane` would
   therefore count the same feedstock twice.
3. **`IAM` is Mt, not PJ.** It is a material demand, so `exported_unit_check`
   would reject it — the same guard that caught the `TNDF` Btkm export.

What is genuinely *not* soft-linked is the ammonia **product** (0.319 Mt/a until
2025); its **energy** is. Revisit only if a scenario keeps ammonia production
past 2025 *and* the coupling wants PyPSA's Haber-Bosch route to build it, in
which case the plant's gas must be removed from `methane` at the same time.
- **Domestic navigation** is the ships' diesel `VAR_FIn` (final energy), and that
  same diesel is subtracted from `total road`, which counts it at the fuel tech.
  `TNDF` (Btkm) is the service and is deliberately **not** exported; it still
  appears on the Sankey as a magenta `U · domestic navigation` sink because it is
  a FOut-only activity commodity.
- **Parent + child categories** (e.g. `BEWAL residential rural heat` ⊇
  `residential rural gas boiler`) tag the same TIMES flow under two categories by
  design — a hierarchy, not a double-count (do not sum parent and children).
  ~104 PJ of exported rows (2030) carry two categories; `qa_parent_child_*.csv`
  validates the identities.

### Balances / loops (QA tables)

- **Comnet residuals: resolved 2026-07-25 — they were a missing value read as a
  zero.** `VAR_Comnet` is only present for the commodities GDX2VEDA was asked to
  export. In the reference `.vd` that is **91 codes, all of them emission or
  pollutant aggregates** (`*CO2N`, `*GHG`, `*NOX`, `*SOX`, `*PM2`, …) — there is no
  energy carrier at all, not even `ELCHIG` or `GASNAT`. The check nevertheless
  looked up every PJ commodity and defaulted the absent value to 0, so the whole
  `ΣFOut` of each `.DEM.` service commodity came back as a residual:

  | 2030 | residual PJ | what it is |
  |---|---:|---|
  | `TAIF` / `TAIP` / `TADF` / `TADP` | 30.37 | aviation service commodities |
  | `COSE`, `COEL`, `CPLI`, `CREF` | 18.74 | commercial data-centre / appliance / lighting / refrigeration services |
  | `ROEL`, `RLIG`, `RREF`, `RCOK`, `RCWA`, `RCDR`, `RDWA` | 20.83 | residential appliance / lighting / cooking services |
  | `NEO` | 6.06 | non-energy consumption (others) |
  | `RHN*` / `RWN*` hubs, `COMELC`, `AGRAP` | 1.1 | real small hub imbalances, already reported by `commodity_node_residuals` |
  | **total** | **77.3** (95.4 in 2050) | |

  `commodity_balance_vs_comnet` now only compares commodities that appear in
  `VAR_Comnet`, and `generate_qa_report` no longer restricts it to PJ carriers, so
  the check actually validates the emission accounting instead of nothing: **all
  71 (2030) / 73 (2050) reported aggregates balance to < 1e-3 PJ.** Energy-carrier
  balances belong to `qa_commodity_residuals_*.csv` and the inflow/outflow ratio
  tables; the genuine small hub imbalances (`Electricity for Commercial sector`
  0.08–0.34 PJ, the new-build `RHN*`/`RWN*` heat and hot-water hubs) show up there
  and as the `Unbalanced <10%` collapse warnings.
- **Surviving loops: one, and it is physical** (resolved 2026-07-25). Loop
  detection runs on the **netted raw TIMES flows**, so a component can never be an
  aggregation artefact — the earlier "vs aggregation artefacts" wording was wrong.
  Restricting `find_loop_components` to energy-carrier flows drops 3 components to
  1:

  | Component | Verdict |
  |---|---|
  | `MISSCR` ↔ `MISCST` via `IISELAFUR00/01` and `IISFINELC01` | real **steel scrap recycling** (1.77 Mt scrap → 1.61 Mt crude steel, 0.17 Mt back from finishing), but both commodities are `.MAT.` in **Mt** — not an energy loop. Now filtered out. |
  | `INDHET` / `INDHTH` / `IOIHTH` via `IOIDEMAND00`, `INDHTH00`, `IOISTMHET01` | real **waste-heat recovery**: `IOIDEMAND00` consumes 4.84 PJ of steam and returns 0.081 PJ (1.7%) to the HT-heat pool. Drops out with the energy filter because these industrial heat commodities have no `pypsa_carrier` (part of the ~37 PJ unmapped-heat coverage gap). |
  | `IPPPUPCHE01` → `INDBLQ` → `CHPINDBLQIPPN00_N` → `INDELC` + `IPPHTH` → `IPPPUPCHE01` / `IPPPACELC01` | real **pulp-mill cogeneration feedback** (2050: the mill buys 0.76 PJ electricity + 1.32 PJ heat, produces 10.20 PJ of black liquor, and its CHP burns 6.21 PJ of it back into 0.57 PJ electricity + 4.63 PJ heat). The apparent energy gain comes from the wood pulped, a `.MAT.` input off the energy Sankey. This is the same closed loop that justifies excluding black liquor from `solid biomass`. |
- **Topology.** For the reference `.vd`/`.vdt` there are **zero** mismatches after
  ignoring `process_code='-'` (GHG aggregates). Re-check
  `qa_topology_mismatches_*.csv` on a new pair.

### Sankey display / classification

- **CHP.** `ETSTP_TVC_WST_E11` is `Type=CHP` but described “Pure ELC” (waste
  condensation turbine); dummy commercial heat `CHSADUM` is unmapped. Whether
  waste-to-energy belongs under CHP or Power plants is open.
- **Commercial electricity** is soft-linked as a single `total electricity
  services` total — deliberately, and the question is **closed** since
  2026-07-25: pypsa-wal scales the Walloon electricity load on that single scalar
  and the Walloon build has no cooling / lighting / appliance / refrigeration
  carrier at all. Data centres are the one end-use split out, as an informational
  child (see § below). The non-electric side of `commercial other` is now
  `services other fuel`; `COEN` itself (the `Com.Other Energy` service commodity)
  and some retrofit dummies stay unmapped / context.
- **EV charging.** Road electricity is picked up at `Fuel Tech - Electricity
  (TRA)` (`ELCLOW`, trucks/rail) and at the home/work chargers (`RSDELC`/`COMELC`
  → `EV charger`, car fleet), so both legs are coloured Transport where they leave
  the grid. Vehicle-km (`TCAR`, …) are unmapped non-energy sinks (expected).
- **Retrofit.** `Retrofitting improvements` produces useful-heat `VAR_FOut` from
  non-carrier dummy option commodities, so it appears with no energy `VAR_FIn`
  (demand-side efficiency accounting, not a fuel import). How PyPSA should treat
  retrofit heat (supply / demand reduction / efficiency signal) is open.
- **LED lighting** `RLIG*` has FOut/FIn = 5.0 (useful lighting service ≫
  electricity) — TIMES service accounting, not a unit error; same COP-style
  pattern as heat pumps.
- **Wallonia biogas** (methanisation + upgrade, ~8 TWh by 2050) is shown under
  Local production (standing in for the Mt feedstocks off the energy Sankey);
  whether it becomes a PyPSA local supply is a coupling-design choice.
- **Electricity storage** discharge spreads across the non-storage consumers of
  the pooled HV electricity commodity (`ELCHIG`) — correct for a pooled commodity;
  storage↔storage collapse links are dropped as artefacts.
- **`sankey_overview` mixed clusters.** Coarse carrier clusters that mix grid
  fuels with FOut-only end-uses (Oil products / Electricity) can show
  ΣFOut ≫ ΣFIn residuals; FOut-only hubs become magenta `U ·` sinks.

## Change log (aggregation / Sankey understanding)

| Date | Change | Reason |
|------|--------|--------|
| 2026-07-25 | **New category `services other fuel`** (0.867 PJ 2030 / 0.873 PJ 2050): the non-electric legs of `Com.Other Energy` plus gas/LPG commercial cooking, read at `demand_input` on `commercial other` with the seven non-electric commercial carriers | The last unmatched tertiary energy. Reading the pypsa side showed the blocker was not the substitution mechanism but that `total services cooking` / `total services` are read by **nothing** in pypsa-wal or pypsa-eur, so that route would have closed the coverage table without delivering any energy. The category is now explicit; one changed line in `write_wallon_heat_demands` serves it |
| 2026-07-25 | **`total electricity services` will not be split further** — closed as a *no* rather than left open | pypsa-wal consumes it as one scalar scaling the whole Walloon electricity load, and there is no cooling / lighting / appliance / refrigeration carrier anywhere in the Walloon build (`cooling` appears in `prepare_sector_network.py` only for the EV cabin temperature correction) |
| 2026-07-25 | **`ammonia` / `methanol` known-zero justifications rewritten** | `methanol` really is structurally absent, but `ammonia` is not: `IAM` (Ammonia Demand, Mt) is met at 0.319 Mt/a until 2025. Zero is still right — the scenario retires the plant before the first coupled horizon, its gas and electricity are already inside `methane` / `electricity`, and pypsa-eur removes the ammonia feedstock from industrial methane before loading `ammonia` — but for none of the reasons the old note gave |
| 2026-07-25 | **`electricity` no longer reads `Fuel Tech - Solar (IND)` / `Renewable: Solar`** | `INDSOL00` is a 1:1 adaptor upstream of `INDPVELC`, so both legs counted the same on-site PV; −0.222 TWh 2025–2035 |
| 2026-07-25 | **`COSEELC100`/`COSEELC101` relabelled `Commercial data centres`; new child category `services data centre electricity`** | Data centres are 32% of `total electricity services` in 2030 and 49% in 2050; the parent total is unchanged and stays the single scalar pypsa-wal consumes |
| 2026-07-25 | **Loop detection restricted to energy-carrier flows** (3 components → 1) | The steel `MISSCR`↔`MISCST` recycle is `.MAT.`/Mt, not energy; the remaining pulp-mill cogeneration feedback is physical. Loops are found at raw TIMES resolution, so none of them could ever have been an aggregation artefact |
| 2026-07-25 | **`commodity_balance_vs_comnet` only compares commodities `VAR_Comnet` reports**, and the QA call drops the PJ-only filter | `VAR_Comnet` holds emission aggregates only in this `.vd`; defaulting the absent value to 0 turned every `.DEM.` service commodity into a 77–95 PJ fake residual, while the aggregates it *does* cover were never checked (they balance exactly) |
| 2026-07-25 | **Aviation rules read the kerosene `VAR_FIn`** instead of the `TADF`/`TADP`/`TAIF`/`TAIP` service `VAR_FOut` (values unchanged) | pypsa-wal loads the two aviation categories onto a `kerosene for aviation` bus, so PyPSA wants the fuel; the FOut only matched because efficiency is exactly 1.000, and no non-heat rule should read a service |
| 2026-07-25 | **Commodity collapse and reciprocal netting are keyed on `export_status`** — a node pair carrying both tagged and untagged energy becomes two ribbons instead of one promoted to `exported` | `merge_export_statuses(any_exported=True)` coloured untagged `Commercial cooling` riding `Commercial Heat pump → Commercial buildings`; Services was over-coloured by 0.15–0.27 TWh. Reconciliation is now exact for Services, Residential and (2050) Industry |
| 2026-07-25 | **`INDCOA00` / `INDCOK00` Aggregation Level 2 unswapped**; `Non-energy` gas and coal feedstock moved out of `naphtha` into `methane` / `coal` | The mapping label contradicted the TIMES Description on the same row, so `coal` read coke and `coke` read hard coal; `naphtha` is PyPSA's *oil* bus and should not carry gas or coal |
| 2026-07-25 | **`residential cooking`**: `RCOKCOA100`/`RCOKELC100` → `residential cooking`, `INFPRCCOK01` → `Industry`, new child category `residential cooking electricity` | Two `.00.Stove.` siblings had drifted to `residential other` so electric cooking was inside `total electricity residential`; an industrial process-heat tech had landed in a residential category on a `COK`/cooking name clash |
| 2026-07-25 | **`electricity road` / `total road` meter EV charging at the charger input** (`process_agg` `TRA_STG_PJ_GW` → `EV charger`) | The two legs are separate fleets (trucks/rail on `ELCLOW`, cars via home/work chargers), metered at different depths; the car leg was read past the charger so 1.25 PJ of charger loss (2050) was in no category |
| 2026-07-25 | **`Exports` split out of `Imports & trade`** on `custom` / `sankey_overview`; `ERNW_PV-*` / `COMSOL00` / `RSDSOL00` overview label `PV` → `Power plants` | One node held both trade directions, so the Sankey drew ribbons running *into* a source (2022: 10.9 PJ pellets, 1.4 logs, 28.7 HV electricity). The PV overview labels were the level-inversion (`custom` is what splits generation) and freed the ≤20-node budget |
| 2026-07-25 | **`INDBLQ` unit corrected `MT` → `PJ`** and its exclusion from `solid biomass` written into the rule `note`; new cross-check of every mapped `Unit` against `AllCommodities.csv` | The audit's `MT` contradicted the VEDA dictionary (`.NRG.`, PJ) and made an excluded *energy* flow look like a material; `INDBLQ` was the file's only unit disagreement |
| 2026-07-25 | **Anchoring made conservation-checked** (`ANCHOR_BALANCE_TOLERANCE` = 10%): a gateway is anchored only when its outputs carry the same energy as its soft-linked input; otherwise the highlight stays upstream. Gateway→gateway chain links are no longer highlighted. `matched_categories` is carried onto anchored links; a `double_count` status is no longer masked by anchoring. Ledger written to `qa_anchor_ledger_{year}.csv`. | `Fuel Tech - H2` (electrolyser, ratio 0.69) coloured 1.20 PJ of *industrial* hydrogen as Transport and again as Industry — the same molecules twice in two sectors. Unconditional anchoring could also lose up to 19% of a highlight to threshold truncation, or colour all outputs of a partly-tagged gateway |
| 2026-07-25 | **New reconciliation check** `export_reconciliation` + `qa_export_reconciliation_{year}.csv`: coloured Sankey PJ vs tagged export PJ, per sector | Nothing in the codebase compared the diagram to the rules it draws, so every defect above was invisible; the ~99% *aggregate* match was a net of offsetting errors in both directions |
| 2026-07-25 | **`total domestic navigation` now reads the ships' diesel `VAR_FIn`** (0.572 PJ 2030) instead of the `TNDF` Btkm activity (1.617), and that diesel is subtracted from `total road`; the `adjust` column now drives every subtraction | PyPSA must receive **final energy** per sector. A Btkm service value in the PJ column implied 283% efficiency, and the fuel tech that serves road, rail and ships counted the ship diesel too |
| 2026-07-25 | Mapping labels: 16 commercial electric heaters → `Commercial electrical stove`, 3 `COMSOL` processes → `Commercial solar thermal`, 6 retrofits `Dum_Retrofit_Commercial` → `Retrofitting improvements`; dropped `Aggregation Level 1` / `PyPSA technology` / `Notes`; `TNDF`→BTKM, `INDBLQ`→MT | Same technology carried two labels depending on vintage, so services heat was understated ~40% in 2050 and `retro` by 7.6%; a uniform `Unit="PJ"` could never catch a Btkm export |
| 2026-07-25 | `extraction_rules.csv` rewritten so every declared filter is applied; emissions dropped before every rule; unknown filter field raises | 9 of 55 rules declared a carrier filter that never ran; only `pypsa_carrier=Heat` kept CO₂ out of the heat rules (2000.8 PJ if dropped); a typo'd field applied no filter at all |
| 2026-07-25 | **New inflow/outflow ratio tables** (`process_io_ratios`): ΣFOut/ΣFIn per TIMES process and per aggregated Sankey node, with `unmapped_in`/`unmapped_out` and a `reading` column | Efficiencies, COPs and energy balances in one place; immediately surfaced `BIOPEL`/`BIOSLU`/`NREWST` as unmapped inputs making waste/pellet fuel techs look like primary sources |
| 2026-07-25 | Coverage-gap numbers re-measured (290/260 → **356/331 PJ**) and the split restated as ~76% consumption-side / ~20% unmapped-or-material / ~5% residual | The old figures were stale by 23–27% and the "almost all consumption-side" claim hid ~40 PJ of *unmapped* industrial heat commodities |
| 2026-07-24 | **Export strategy rework**: colour exported links by PyPSA sector (Industry/Transport/Residential/Services/Agriculture) and anchor soft-linked fuel `VAR_FIn` to the demand inflow (`assign_export_sectors`). Removed single-blue / light-red-mixed colouring. | Exports were scattered (FIn near supply, FOut near demand) and indistinguishable (all blue); mixed verified never to occur |
| 2026-07-24 | Coverage-gap / double-count fixes: added `residential cooking` rule (~1.4→4.6 PJ/yr); fixed biofuel-blending double-count in `total road` (`road_internal_transfer_pj`, −5 PJ 2030 → −0.3 PJ 2050). Only these two categories change in `wallon_demands_*.csv`. | Sankey purpose: catch forgotten flows / double counts |
| 2026-07-24 | Keep Cars / Road Freight / Road transport (public) / 2–3 wheelers as context labels; drop electricity storage↔storage collapse links | RHS `Transport (other)` was opaque mix of modes; Pumped hydro→Grid battery was ELCHIG proportional artifact |
| 2026-07-22 | Move digestor diesel / black-liquor / biofuel synthesis PRE out of Local production → Fuel conversion | LP must stay primary; diesel & INDBLQ were carrier FIn into LP |
| 2026-07-22 | Biogas under Local production; EV chargers + EV battery storage renames | LP replaces Mt feedstocks; clarify TRA_STG path |
| 2026-07-22 | Rename Fuel refining→Fuel conversion; map `BWSUPGZH100`/`BIOSLUH`; storage nodes + split HV/residential electricity | Biogas≈8 TWh Wallonia potential; storage charge/discharge invisible under PP |
| 2026-07-21 | ELC `ELC*00` fuel techs: Power plants → `Fuel tech · …` | Fake PP→CHP fuel ribbon; PP should mainly show electricity |
| 2026-07-21 | Split commercial other + residential/commercial buildings on `custom`; map `CLIG*`/`COEL`; fix `TNAINLBDLN01` → domestic navigation; document navigation soft-link | Notes: nav “missing”; appliances→Buildings mix; HX→Buildings |
| 2026-07-21 | Map `BATELCIN`; distinct `BATELCOUT`/`INDELC`/`OILDST`/kerosene/`BIOCPS` customs; refine rejects `{family} (context)` as export-hub labels; fuel-tech display split kerosene/oil/diesel/gasoline | TO→Industry + naphtha↔diesel artifacts; TRA_STG swallowed by Transport (other) |
| 2026-07-21 | Heat pumps: `sankey_overview` CHP→Buildings; `infer_overview_process_label` excludes heat-pump / `ELCHP`; refine no longer lets overview `CHP` override a specific custom label | CHP node mixed heat pumps (COP) with true CHP → FOut ≫ FIn |
| 2026-07-21 | Map commodities `SUPGMX`, `BIOGAS`, `BIOGZH` | Methane fuel techs lost FIn under energy filter → looked primary |
| 2026-07-21 | Map `ELCPEL`, `ELCWST`, `ELCSLU` and industrial HTH `IPPHTH`/`IOFHTH`/`IBOHTH`/`IPOHTH`/`IMLHTH`/`INMHTH` | True CHP fuel in / process-heat out were dropped |
| 2026-07-21 | Document CHP + methane findings and open TIMES questions (this section) | Keep uncertainties visible; no silent “fixes” |
| 2026-07-21 | Map `IMPH2`; rename `hydrogen imports` → `imported H2 delivery`; overview Fuel conversion | Missing IMPH2 hid H₂ FIn; electricity is real compression |
| 2026-07-21 | Map `RSDAHT`/`COMAHT`; fix `CC*` to Commercial cooling | Residential Sankey COP; cooling mislabelled as appliances |
| 2026-07-21 | `RSDSOL00`/`COMSOL00` → PV; map `INDHTH`/`RENMINGEO` | LTH FOut≫FIn was solar→PV feedstock mis-bucketed as heat |
| 2026-07-21 | Map `RSDPEL`/`COMPEL`; move `RSDLIONBATS01` out of household appliances; document LED lighting 5× | Appliances/biomass FOut>FIn: lighting service + missing pellets + battery mislabel |
