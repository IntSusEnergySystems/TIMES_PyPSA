# Aggregation levels and Sankey colouring

Detailed reference for TIMES → PyPSA Sankey aggregation (`--agg-level`), commodity-hub collapse, export colouring, and the `custom` working level. CLI overview and install remain in [README.md](README.md).

## Aggregation levels (Sankey / QA only)

Extraction itself is unchanged (filters on Aggregation Level 2 labels). Sankey / QA aggregation is selected by a **single shared CSV column name** (process mapping × commodity mapping).

| Level (`--agg-level`) | Process nodes | Commodity nodes | Typical node count | Use |
|-----------------------|---------------|-----------------|--------------------|-----|
| **`Aggregation Level 2`** (default; alias `mapping`) | Aggregation Level 2 | Aggregation Level 2 (= PyPSA Energy Carrier) | hundreds | Export neighbourhood / detailed QA |
| **`custom`** | Export-touching Aggregation Level 2 (+ friendly renames); else overview / `(other)` | Export-touching commodity codes (specific labels); else `{family} (context)` | **~45** after collapse | **Working level**: readable whole-system + blue soft-link paths |
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

| Element | Colour | Meaning |
|---------|--------|---------|
| Process / commodity **nodes** | green family | ordinary Sankey nodes (no P/C prefix; commodities are flows after collapse) |
| Imbalance **nodes** | magenta family | `U ·` demand / residual hubs (expected or unexplained) |
| Link exported | blue | exactly one of FOut/FIn endpoints is sent to PyPSA |
| Link double-count | purple | **both** FOut and FIn endpoints exported (soft-link overlap risk) |
| Link mixed | light red | a single endpoint already mixes exported + non-exported rows |
| Link context | grey | unrelated to PyPSA export |

**Plotly limitation:** Sankey links only support a **single solid colour** per ribbon — there is no source→target gradient. Ideal left=FOut / right=FIn colouring is therefore not available; purple marks the both-exported case instead.

Internal node ids are typed (`process::Label` / `imbalance::Label`) so names never
collide. Optional bipartite view (commodity nodes kept) is still available via
`collapse_commodities=False` on the prepare helpers.

## Designing `custom` (working level)

`custom` is the **editable working aggregation** for export QA. Goal: a **readable** whole-system Sankey that still shows PyPSA-exported flows as **blue** links at soft-link resolution.

Extraction filters on **Aggregation Level 2** (`process_agg` in `extraction_rules.csv`), not on individual process Descriptions. Description-level nodes are therefore over-detailed for the soft-link objective and make the diagram unreadable once small links are kept (threshold 0).

### Runtime rules (when flows are export-tagged)

`aggregate_flows(..., level="custom")` applies `refine_custom_labels_for_readability` on top of the CSV `custom` column:

| Side | Keep individual | Collapse |
|------|-----------------|----------|
| **Processes** | **Supply-chain roles** (parallel, same direction): `Imports & trade`, `Local production` (MIN*), `Fuel refining`, `Power plants` (incl. ELC generation fuel-tech). **Plus** Aggregation Level 2 labels on any exported row (soft-link grain, with friendly renames). | Remaining context → `End-use fuel tech`, sector `(other)`, … — never merge upstream primary with downstream end-use |
| **Commodities** | Export-touching codes keep one specific label for all rows of that code | Other carriers → `{family} (context)` |

After commodity-hub collapse, **reciprocal process↔process ribbons are netted** when the netting toggle is on (`net_collapsed_process_links`), so A→B and B→A from coarse hubs do not appear as loops.

**Friendly process renames**

| Aggregation Level 2 | `custom` display |
|---------------------|------------------|
| `residential other` | Household electrical appliances |
| `commercial other` | Commercial electrical appliances |
| `Retrofitting improvements` | Building retrofits |

**Supply-chain roles (do not merge across steps)**

| Label | Meaning |
|-------|---------|
| Imports & trade | `.IMP.IRE.` / import processes |
| Local production | `.MIN.IRE.` domestic potentials (solar/wind/biomass/…) |
| Fuel refining | SUP synthesis / refining |
| Power plants | Generation (thermal, hydro, …) — **excluding** PV / onshore wind splits below |
| PV | Utility + rooftop / sector PV generation (`ERNW_PV*`, `*PVELC`, solar fuel-tech ELC) |
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

**Do not drop these flows.** Soft-linking is universal: every matched export flow stays in QA views. If the left-side placement remains confusing, improve labels/layout — never hide the soft-link.

Open modeller note: whether PyPSA should receive retrofit useful-heat FOut as a positive heat supply, a demand reduction, or a separate efficiency signal is a coupling-design question; the extractor must still show what TIMES reports.

### Export link colours (blue vs light red)

- **Blue**: collapsed link has soft-link mass on the path (`any_exported`: exactly one endpoint exported, or both → purple double-count).
- **Light red (mixed)**: a single aggregated endpoint still mixes exported + non-exported *rows* before collapse.
- **Grey**: no export on either endpoint.

Blue PJ on the Sankey is **path energy**, not a 1:1 sum of tagged export rows. Some tagged export mass ends in magenta residual sinks (FOut-only DEM / hub imbalance) rather than a process→process ribbon — that is why blue share can be below 100% of tagged export PJ without those leftovers appearing light red.

Edit `custom` freely in:

- `data/mapping_processes.csv`
- `data/mapping_commodities.csv`

then re-run QA with `--agg-level custom`. Use `qa_sankey_label_map_{year}.csv` to see which TIMES codes sit under each custom label.

### Collapse colouring rule (any-exported)

After commodity-hub collapse, each Sankey link is one process→process energy transfer
(the matched `min(ΣFOut, ΣFIn)` portion of a commodity hub).

**Rule:**
- exactly one endpoint `exported` → **blue**
- both endpoints `exported` → **purple** (`double_count`; FOut and FIn both soft-linked)
- neither → grey; endpoint-internal mix → light red

Rationale: PyPSA cares that the soft-link quantity is on the path; the upstream
import or downstream building that closes the commodity balance is neighbourhood
context. Requiring both ends exported painted most links light-red; requiring
neither paints the true soft-link path grey. Purple flags the rare (undesired)
case where both sides of the same commodity transfer are in `extraction_rules.csv`.

### What becomes blue

With `--agg-level custom` and netting, **every** aggregated export-neighbourhood row is either `exported` (blue) or `context` (grey) — no `mixed` from label collision. In 2030 that is ~376 PJ of pure exported rows before commodity-hub collapse (~320 distinct process×commodity pairs).

After collapse with the any-exported rule, typical export neighbourhood links are blue when they carry a soft-link quantity, including:

#### Import / supply → exported fuel-tech (now blue)

Soft-link rules often export the **fuel input** (`VAR_FIn`) into a fuel-tech process; the upstream import (`VAR_FOut`) is context. Collapse forms one flow → **blue**.

**Example — diesel for road (~49.8 PJ)**

| Role | TIMES code | Attribute | Commodity | Exported? | Rule |
|------|------------|-----------|-----------|-----------|------|
| Producer | `IMPOILDST` (Import Diesel) | `VAR_FOut` | `OILDST` (Diesel) | no | — |
| Consumer | `TRADST00` (Fuel Tech - Diesel TRA) | `VAR_FIn` | `OILDST` | **yes** | `total road` |

Collapsed link: `Imports` → `Fuel Tech - Diesel (TRA)` · `Diesel [OILDST]` · **exported (blue)**.

#### Heat tech → buildings (now blue)

BEWAL / boiler rules export heat **FOut**; building **FIn** of the same heat commodity is not exported. One exported endpoint → **blue**.

**Example — rural condensing gas stove (~11.3 PJ)**

| Role | Code | Flow | Exported? |
|------|------|------|-----------|
| Producer | `RH4FGMXN1` | FOut `RH4F` (space heating 4f) | **yes** (`BEWAL…`, `residential rural gas boiler`) |
| Consumer | `RDW_R_4Fac` | FIn `RH4F` | no |

Collapsed link: heater → `Buildings: built area` · **exported (blue)**.
(The heater’s gas FIn `RSDGMX` is a separate commodity link and stays grey.)

### What can still be mixed / not “individual” via aggregation alone

#### 1. Same process × commodity already mixed before pairing

If one process has both exported and non-exported rows for the **same** commodity label, that endpoint is `mixed`, and the collapsed link stays mixed.

#### 2. Parent + child double-tagging (~104 PJ of exported *rows*)

One TIMES flow matches two extraction categories (parent ⊇ child). The row is exported once; hover lists both categories. Aggregation cannot draw two Sankey links from one physical flow.

**Examples (2030)**

| Process | Flow | PJ | Categories |
|---------|------|-----|------------|
| `RH4FGMXN1` | FOut `RH4F` | 11.35 | `BEWAL residential rural heat` **and** `residential rural gas boiler` |
| `RH2FGMX100` | FOut `RH2F` | 5.65 | `BEWAL residential urban decentral heat` **and** `residential urban decentral gas boiler` |
| `TSTGEVCMIDN01` | FIn `BATELCIN` | 4.91 | `electricity road` **and** `total road` |

#### 3. Changing `custom` does not change PyPSA demand CSVs

Extraction still filters on Aggregation Level 2 / `process_agg` + `extraction_rules.csv`. `custom` only affects Sankey / QA display.

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
4. Aggregate with the selected `--agg-level` labels; colour blue = exported, grey = context, light red = mixed

---

## Investigated Sankey issues (`custom`)

Working notes while reading the whole-system energy Sankey. Goal: understand first, then fix aggregation / mappings; leave explicit open points for TIMES experts. Do not drop soft-link flows to “clean” the diagram.

### CHP: small inputs, large outputs

**What it looked like.** The `CHP` node had much more FOut than FIn (energy conservation apparently violated).

**Causes (stacking):**

1. **Heat pumps wrongly under `CHP`.** Many residential/commercial heat-pump codes contain the substring `ELCHP` / `CHPN` (electric heat pump). `sankey_overview` was incorrectly set to `CHP`, and `refine_custom_labels_for_readability` preferred that overview supply-chain label over the correct `custom` / L2 heat-pump label. Heat pumps have COP ≫ 1, so they inflate CHP outputs vs electricity inputs. Ambient heat (`RSDAHT`) is still unmapped (no `pypsa_carrier`), so even correctly labelled heat pumps can look “creative” of energy.
2. **Missing fuel / process-heat commodities.** The energy Sankey keeps only rows with a mapped `pypsa_carrier`. Fuels `ELCPEL` / `ELCWST` / `ELCSLU` and industrial CHP heat `IPPHTH` / `IOFHTH` / … were absent from `mapping_commodities.csv`, so true CHP plants lost fuel FIn and/or useful-heat FOut.
3. **CO₂ / pollutants** dominate raw FOut if the carrier filter is skipped — QA already drops them via `filter_energy_carrier_flows`.

**Fixes applied.** Heat-pump `sankey_overview` → `Buildings`; heuristic + refine no longer classify `*ELCHP*` / “heat pump” as CHP; map the missing ELC fuels and industrial HTH commodities. After fix (2030 energy view): CHP ≈ 30 PJ FIn / 19 PJ FOut (out/in ≈ 0.63) — **FIn > FOut is expected** (conversion losses); the previous FOut ≫ FIn was the artifact.

**Still open.** `ETSTP_TVC_WST_E11` is `Type=CHP` in the TIMES dictionary but described as “Pure ELC” (it does emit some `ELCHET`). Dummy commercial heat `CHSADUM` remains unmapped. Ambient heat `RSDAHT` still unmapped (heat-pump COP on the Sankey). Confirm with TIMES experts whether waste-to-energy should stay under CHP vs Power plants.

### Fuel tech · methane: looks like primary energy

**What it looked like.** `Fuel tech · methane` had large FOut (sector network gas) and no Sankey inputs.

**Cause.** End-use gas fuel techs (`RSDGMX00`, `COMGMX00`, `INDGMX00`, …) take **`SUPGMX`** (and some **`BIOGAS`**). Those commodities were **not in** `mapping_commodities.csv`, so the energy filter dropped all methane fuel-tech FIn while sector outputs (`RSDGMX`, `COMGMX`, …) stayed. Not a missing import on the Sankey side of the fuel tech — the import is one step upstream:

`IMPGASNAT` → `GASNAT` → `SUPGMX00` (+ upgraded `BIOGZH`) → **`SUPGMX`** → end-use fuel techs → sector gas → boilers.

Local biogas: methanisation → `BIOGAS` → upgrade → `BIOGZH` → same mix.

**Fixes applied.** Map `SUPGMX`, `BIOGAS`, `BIOGZH` (and keep Imports / Fuel refining as the upstream nodes).

**Still open.** Whether `BIOEFF` (effluents, Mt) should ever appear on an energy Sankey (currently excluded — no PJ carrier). Soft-link methane rules still export **VAR_FIn** into industry/TRA gas fuel techs; residential/commercial `Fuel tech · methane` is mostly context unless those L2 labels are in `extraction_rules.csv`.

---

## Change log (aggregation / Sankey understanding)

| Date | Change | Reason |
|------|--------|--------|
| 2026-07-21 | Heat pumps: `sankey_overview` CHP→Buildings; `infer_overview_process_label` excludes heat-pump / `ELCHP`; refine no longer lets overview `CHP` override a specific custom label | CHP node mixed heat pumps (COP) with true CHP → FOut ≫ FIn |
| 2026-07-21 | Map commodities `SUPGMX`, `BIOGAS`, `BIOGZH` | Methane fuel techs lost FIn under energy filter → looked primary |
| 2026-07-21 | Map `ELCPEL`, `ELCWST`, `ELCSLU` and industrial HTH `IPPHTH`/`IOFHTH`/`IBOHTH`/`IPOHTH`/`IMLHTH`/`INMHTH` | True CHP fuel in / process-heat out were dropped |
| 2026-07-21 | Document CHP + methane findings and open TIMES questions (this section) | Keep uncertainties visible; no silent “fixes” |
