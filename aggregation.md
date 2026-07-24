# Aggregation levels and Sankey colouring

Detailed reference for TIMES → PyPSA Sankey aggregation (`--agg-level`), commodity-hub collapse, export colouring, and the `custom` working level. CLI overview and install remain in [README.md](README.md).

## Aggregation levels (Sankey / QA only)

Extraction itself is unchanged (filters on Aggregation Level 2 labels). Sankey / QA aggregation is selected by a **single shared CSV column name** (process mapping × commodity mapping).

| Level (`--agg-level`) | Process nodes | Commodity nodes | Typical node count | Use |
|-----------------------|---------------|-----------------|--------------------|-----|
| **`Aggregation Level 2`** (default; alias `mapping`) | Aggregation Level 2 | Aggregation Level 2 (= PyPSA Energy Carrier) | hundreds | Export neighbourhood / detailed QA |
| **`custom`** | Export-touching Aggregation Level 2 (+ friendly renames); else overview / `(other)` | Export-touching commodity codes (specific labels); else `{family} (context)` | **~45** after collapse | **Working level**: readable whole-system + sector-coloured soft-link paths |
| `Aggregation Level 1` | Aggregation Level 1 | Aggregation Level 1 (= Cluster) | high | Mid drill-down |
| `Sector` (alias `L0`) | Sector codes | Sector codes | ~15 | Coarse sector check |
| **`sankey_overview`** | Overview process clusters | Overview carriers | **≤20** | Readable whole-system Sankey |
| `L2` | `process_code` | `commodity_code` | thousands | Fine drill-down (no collapse) |

**No opaque placeholders.** Empty mapping cells fall back to TIMES Description, then code — never `Unknown`.

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

### 2. Anchor soft-linked fuel inputs to the demand inflow

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

Because these fuel-delivery techs are ~1:1 (input ≈ output — verified: e.g.
`Fuel Tech - Electricity (IND)` 36.7 PJ in / 36.7 PJ out to `Industry (other)`;
`Fuel Tech - Diesel (TRA)` 54.8 PJ in / 54.7 PJ out split across vehicles), the
highlighted quantity is preserved. Result (`custom`, 2030/2050): every soft-link
now enters an end-use node coloured by sector — e.g. **industry fuel exports sit
on the `Fuel Tech (IND) → Industry (other)` links, not the upstream
import / power-plant links**; transport fuels on `Fuel Tech (TRA) → Cars /
Road Freight`; electricity for industry on `Fuel Tech - Electricity (IND) →
Industry (other)`.

Anchoring also removes a pre-existing **double-highlight**: the biodiesel
blended into diesel was previously drawn blue on both `Imports → Biodiesel` and
`Biodiesel → Diesel`; it is now highlighted once, on the gateway output chain.

Generation nodes (PV, wind, power plants, CHP) are **never** anchored across
their heterogeneous outputs — `_is_anchorable_gateway_label` is restricted to
fuel-delivery techs so a small industry-electricity rule via `PV industrial`
cannot recolour all of PV's grid output.

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
  `double_count`** link, **every exported link has a sector**, and no anchorable
  gateway keeps an exported fuel-supply input.

## Designing `custom` (working level)

`custom` is the **editable working aggregation** for export QA. Goal: a **readable** whole-system Sankey that still shows PyPSA-exported flows as **sector-coloured** links at soft-link resolution.

Extraction filters on **Aggregation Level 2** (`process_agg` in `extraction_rules.csv`), not on individual process Descriptions. Description-level nodes are therefore over-detailed for the soft-link objective and make the diagram unreadable once small links are kept (threshold 0).

### Runtime rules (when flows are export-tagged)

`aggregate_flows(..., level="custom")` applies `refine_custom_labels_for_readability` on top of the CSV `custom` column:

| Side | Keep individual | Collapse |
|------|-----------------|----------|
| **Processes** | **Supply-chain roles** (parallel, same direction): `Imports & trade`, `Local production` (MIN* + Wallonia biogas), `Fuel conversion`, `Power plants`, electricity storage / EV chargers. **Plus** Aggregation Level 2 labels on any exported row (soft-link grain, with friendly renames). | Remaining context → `End-use fuel tech`, sector `(other)`, … — never merge upstream primary with downstream end-use |
| **Commodities** | Export-touching codes keep one specific label for all rows of that code | Other carriers → `{family} (context)` |

After commodity-hub collapse, **reciprocal process↔process ribbons are netted** when the netting toggle is on (`net_collapsed_process_links`), so A→B and B→A from coarse hubs do not appear as loops.

**Friendly process renames**

| Aggregation Level 2 | `custom` display |
|---------------------|------------------|
| `residential other` | Household electrical appliances |
| `commercial other` | Commercial electrical appliances (default); CSV subclasses kept: **Commercial cooling**, **Commercial lighting**, **Commercial cooking** |
| `Retrofitting improvements` | Building retrofits |
| `hydrogen imports` | imported H2 delivery |
| `Buildings: built area` (`RDW_*`) | **Residential buildings** |
| `Building Existing/New *` (`COM_CBAT_*`) | **Commercial buildings** |
| `residential cooking` | Residential cooking |

**Supply-chain roles (do not merge across steps)**

| Label | Meaning |
|-------|---------|
| Imports & trade | `.IMP.IRE.` / import processes |
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
`context` is grey. Coloured PJ is **path energy** after collapse/netting, not a
1:1 sum of tagged export rows — some tagged mass ends in magenta `U ·` sinks
(FOut-only DEM / hub imbalance) rather than a process→process ribbon, so the
coloured share can sit below 100% of tagged export PJ.

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
- Extraction rules are independent: they still filter on Aggregation Level 2 / `process_agg`. Changing `sankey_overview` or `custom` does not change PyPSA demand exports.
- Legacy aliases: `L0`→`Sector`, `L1`→`Aggregation Level 1`, `mapping`→`Aggregation Level 2`, `L2`→raw codes.

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

The DMD coverage gap (`qa_coverage_gap_*.csv`) is ~290 PJ (2030) / ~260 PJ
(2050), but almost all of it is **consumption-side (n+1) of already-exported
production** and must stay out to avoid double-counting:

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

### Extraction rules / demand CSVs

- **Aviation kerosene FIN-vs-FOUT.** `total (domestic|international) aviation`
  export the service `VAR_FOut` (`TAIF`/`TAIP`, treated as PJ), parallel to
  navigation. The kerosene `VAR_FIn` (~30 PJ) stays out — exporting it too would
  double-count. Confirm the FOut convention is what PyPSA expects.
- **`total international navigation` is always 0.** Its filter label
  `international navigation` is absent from `mapping_processes.csv`
  Aggregation Level 2, so the rule matches nothing; the category is on the
  known-zero allowlist. If international navigation should be non-zero, correct
  the label.
- **Known-zero allowlist**: `ammonia`, `methanol`, `total international
  navigation`, `coal` — expected 0 in this scenario (re-check on a new one).
  Several residential coal / biomass / solar / oil boiler categories are 0 in
  2050 but non-zero earlier — expected.
- **Domestic navigation** is exported as FOut-only activity (`TNDF`, Btkm) → a
  magenta `U · domestic navigation` sink; the diesel energy into ships is counted
  under `total road`. Optional refinement: export the diesel `VAR_FIn` into
  navigation and subtract it from `total road`.
- **Parent + child categories** (e.g. `BEWAL residential rural heat` ⊇
  `residential rural gas boiler`) tag the same TIMES flow under two categories by
  design — a hierarchy, not a double-count (do not sum parent and children).
  ~104 PJ of exported rows (2030) carry two categories; `qa_parent_child_*.csv`
  validates the identities.

### Balances / loops (QA tables)

- **Comnet residuals.** After restricting to FIn/FOut-active commodities, some PJ
  carriers still disagree with `VAR_Comnet` (trade / stock / IMPEXP terms outside
  process flows). See `qa_node_balance_*.csv`.
- **Surviving loops after netting** (`qa_loops_*.csv`): industry heat/steam cycle
  (`INDHET`/`INDHTH`), steel scrap / electric-furnace cluster, bio/CHP/electricity
  cluster (`ELCHIG`/`INDELC`) — physical recycles / CHP feedbacks vs aggregation
  artefacts.
- **Topology.** For the reference `.vd`/`.vdt` there are **zero** mismatches after
  ignoring `process_code='-'` (GHG aggregates). Re-check
  `qa_topology_mismatches_*.csv` on a new pair.

### Sankey display / classification

- **CHP.** `ETSTP_TVC_WST_E11` is `Type=CHP` but described “Pure ELC” (waste
  condensation turbine); dummy commercial heat `CHSADUM` is unmapped. Whether
  waste-to-energy belongs under CHP or Power plants is open.
- **Commercial electricity** is soft-linked as a single `total electricity
  services` total; splitting cooling / lighting / appliances is a coupling-design
  choice. `COEN` (Mm²) and some retrofit dummies stay unmapped / context.
- **EV charging.** Home/work charging electricity (`RSDELC`/`COMELC` → chargers)
  stays grey; the road electricity is picked up at `TRA_STG` (`BATELCIN`) +
  `Fuel Tech - Electricity (TRA)`. Vehicle-km (`TCAR`, …) are unmapped non-energy
  sinks (expected).
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
