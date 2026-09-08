# Scenario indicators straight from the `.vd`

`times_pypsa.indicators` reads a TIMES `.vd` and produces the four indicator
families the Walloon scenario report puts on a page — **final energy demand**,
**greenhouse-gas emissions**, **heat production** and the **power fleet** —
without going through PyPSA. `times_pypsa.indicator_pages` renders them as
standalone Plotly HTML pages (one stacked bar per planning horizon, the total on
top, the table it was built from beside it) plus the same tables as CSV — and,
on top, an **Indicateurs** page that puts every series on one filterable table
with a sparkline and a Δ% column (§5b).

This is the *other half* of what the library already does. `sankey_pages`
answers "where does the energy go in year Y"; this answers "what does the
trajectory look like". Both parse the same `.vd`.

- Rules live in [`data/indicator_fuels.csv`](data/indicator_fuels.csv),
  [`data/indicator_heat.csv`](data/indicator_heat.csv) and
  [`data/indicator_power.csv`](data/indicator_power.csv) — a mapping fix is a
  data edit, not a code change.
- Units: **GWh** for energy, **ktCO2eq** for emissions, **GW** for capacity.
- Tests: [`tests/test_indicators.py`](tests/test_indicators.py) and
  [`tests/test_indicator_pages.py`](tests/test_indicator_pages.py).

```bash
times-pypsa indicators --vd data/scen_corrige_251129_0112.vd \
  --out-dir output/indicators --scenario-label "demande haute"
```

`--years` defaults to **every model year in the `.vd`** (2021, 2022, 2025, 2030,
2035, 2040, 2045, 2050 for the Walloon scenarios) and is there to narrow that,
never to complete it. Pass it only to answer a question about specific horizons:
a trajectory chart drawn on the four years PyPSA steps through has holes at 2035
and 2045 and no 2021, which is the year §6 reconciles against.

In pypsa-wal the same thing runs as `rule build_times_indicators`
(`sector.times_indicators.enable`), writing into
`results/<prefix>/<run>/html/indicators/` — the third section of the
`html/index.html` hub, beside `html/pypsa/` and `html/times/`, and published to
`pypsa.squoilin.eu` with the rest of the folder. Five pages are written:
`times_indicators_catalogue.html` (the filterable table, start here) and one
charted page per family.

---

## 1. Where the reference numbers come from

The December-2025 figures in `figures_from_demande_haute_04-12-2025/` are
screenshots of the ClimAct Explorer pages for the scenario
`times-pypsa__demande-haute__20251204`. That S3 folder contains both the `.vd`
those pages were built from and, under `strategy/report/`, **the CSV tables
behind the charts**:

```bash
export AWS_PROFILE=intervectoriel
aws s3 ls s3://intervectoriel/test/scenarios/times-pypsa__demande-haute__20251204/strategy/report/
```

| File | Chart it backs |
|---|---|
| `demande_generale.csv`, `demande_{industrie,tertiaire,residentiel,transport,agriculture}.csv` | *Consommation d'énergie [TIMES]* |
| `emission_sectors.csv`, `emission.csv` | *Emissions CO2 [TIMES]* |
| `TIMES_heat_output.csv` | *Production de chaleur [Times]* |
| `capacite_installee.csv`, `production_brute.csv`, `*_renouvelables.csv` | power fleet |

The `.vd` in the same folder is `scen_corrige_251129_0112.vd`, byte-identical to
the local `data/` copy — so "demande haute, 04-12-2025" **is** `scen_corrige`,
despite the file name not saying so. Everything below was reconciled against
those CSVs rather than against the screenshots, so manual-reading error is out
of the picture.

**Two different solves are involved.** `TIMES_heat_output.csv` names its source
in a column: `scen_corrige_251130_0312` — a solve exported *two days after* the
`.vd` published beside it. The report CSVs were regenerated on 2026-02-17. So the
published tables and the published `.vd` are one model iteration apart. This is
the reason almost every residual below is ~0 % in 2021 (the calibrated base
year, identical in both solves) and grows with the horizon. Treat post-2025
differences as *scenario vintage* unless they are listed as a rule difference in
§5.

---

## 2. Final energy demand

**Measurement point: the sector-owned carrier.** TIMES gives every demand sector
its own copy of each carrier — `RSDGMX` (residential grid gas), `INDELC`
(industrial electricity), `COMOIL` — produced by a one-for-one "fuel tech"
process (`RSDGMX00: SUPGMX → RSDGMX`) and consumed by the end-use devices. The
table sums `VAR_FIn` on those sector carriers.

Metering there rather than on the supply carrier is what makes the rest work:

- a heat pump's electricity stays in the electricity row (it draws `RSDELC`),
  while the heat it delivers is counted only in the heat table;
- the gas a boiler burns is counted, not the heat the boiler makes, so nothing
  is double counted against the heat table;
- district heat bought from a network appears as `Heat`, boiler heat does not.

**Attribution: the sector of the *consuming process*, not of the carrier.** Home
EV charging is a transport process (`TCHARGHOMN01`) drawing `RSDELC`; booking it
by carrier would put the car in the living room. When the process mapping leaves
`Sector` blank — it does for a handful of newer devices, `RW4FGMXN3` and friends,
0.5 TWh of residential gas — the row falls back to the sector that owns the
carrier. A process mapped to a *non-demand* sector stays out: `BBLQH2G110` is a
`SUP` gasifier turning industrial black liquor into synthetic hydrogen, so its
feedstock is a conversion input, not industrial final energy.

**Weights.** Three things draw a sector carrier without consuming final energy:

| Case | Weight | Why |
|---|---|---|
| Rooftop PV (`ELE`), battery storage (`STG`/`STS`) | 0 | `RSDPVELC` draws `RSDSOL` to *make* electricity; `RSDLIONBATS01` draws `RSDELC` to store it. Neither is consumption. |
| Fuel-tech pass-throughs (`Description` starts with "Fuel Tech") | 0 | `RSDOIL00` turns `OILDST` into `RSDOIL` one-for-one. Counting both sides doubles the sector's oil. |
| Cogeneration (`CHP`) | heat share | One fuel stream, two products. The fuel is split in proportion to the outputs; only the heat part is the sector's final energy, the rest is generation. |

The heat share is `heat_out / (heat_out + electricity_out)` per process and
year. `ELCHET` ("Electricity - Heat") counts as **heat** despite the prefix — it
is the district-heat carrier.

**Blended road fuels.** `TRADST` is diesel *after* biodiesel blending, so the
fossil/bio split only exists upstream of it. `TRADST`, `TRAGSL` and `TRAETH` are
marked `passthrough`: their consumption is redistributed over the fuel mix of
the blender's inputs (`OILDST` → Oil products, `TRABDL`/`BIODST` → Biofuel). The
blender conserves energy, so this is a relabelling, not a rescaling.

**Commercial cogeneration.** `ECHPP_COM_*` sits in the TIMES `ELC` sector and
burns `ELC*` carriers, so the sector-carrier rule never sees it — yet its heat
warms tertiary buildings. `heat_to_sector` in `indicator_power.csv` routes the
heat-allocated share of its fuel to Tertiary. In 2021 that is +123 GWh of gas,
+53 GWh of biogas and +0.4 GWh of wood, which is exactly what the published
tertiary table has over the bare `COM*` carriers.

**Aviation kerosene** is reported on its own row and **excluded from the
transport total** (`TOTAL_EXCLUDES`): it is an international bunker, and the
published tables leave it out entirely.

The row is therefore listed in the table's `excluded_rows`, which is what makes
the report draw it as a hatched grey bar *beside* the transport stack rather
than in it, and mark it with a `*` in the table. Stacking it was a real defect:
the bar overshot its own total line by the whole 8.3 TWh bunker, which reads as
an extraction error. `test_totals_are_the_sum_of_the_stacked_rows` and
`test_stacked_traces_add_up_to_the_total_line` hold every table and every
rendered chart to `Σ stacked rows == total`, so a future row that leaves a total
has to declare itself the same way.

## 3. Emissions

```
sector GHG = Σ per-process VAR_FOut of  <SEC>CO2N + <SEC>CO2P
                                      + 28·<SEC>CH4 + 265·<SEC>N2O
```

grouped by the **commodity prefix**, over process-level rows only (TIMES also
writes a region-level row per gas with `process = "-"`; summing both doubles the
answer). Biogenic CO2 (`<SEC>CO2b`) is excluded per the usual inventory
convention. GWPs are AR5 GWP-100.

That formula *is* the model's own `<SEC>GHG` aggregate — the two agree to
floating-point wherever nothing is captured, which is the regression test in
`test_ghg_matches_the_times_ghg_aggregate_where_nothing_is_captured`.

**Why not just read `<SEC>GHG`?** Because from 2035 it stops meaning what it
says. `ELCGHG` 2035 is 3493 ktCO2eq against 1170 of actual stack emissions: the
difference is `ELCCO2c`, CO2 that CCS captured and that the GHG accounting
commodity still carries. Rebuilding the total from the gases nets the capture
out and makes it an explicit second series (`co2_capture`), instead of silently
inflating the industry and electricity rows once CCS starts.

## 4. Heat production

Metered on the **output of the heating device** (the heat service commodity), so
a heat pump shows the heat it delivers rather than the electricity it drew.
This is the number a heating-technology chart needs, and it is why this table
cannot be derived from the demand table.

- **Segments** come from the service commodity: `RH*`/`RHN*`/`RHDJ*` residential
  space heat, `RW*`/`RWN*` residential water, `CH*`/`CHN*` and `CW*`/`CWN*`
  tertiary, `I??HTH` industrial process heat per branch.
- **Technology** comes from the token in the process code, first match wins:
  `^CHP` → Cogeneration, `ELCHP` → Heat pump, `HET` → District heat, `GEO`,
  `SOL` (with a negative lookahead so `…STMSOLID00` is not solar), `ELC` →
  Direct electric heating, everything else → Boiler.

Two exclusions:

- **`Retrofit-*` processes** "produce" the heat service that insulation no
  longer needs, out of a dummy input. That is avoided demand, not production;
  counting it inflates the residential boiler bar by up to 4.2 TWh in 2050.
- **`INDHTH`**, the industry-wide heat aggregate, is redistributed as `INDHET`
  and reappears as branch process heat, so counting it too double counts 0.36
  TWh (2021). The per-branch `I??HTH` commodities are listed explicitly rather
  than by a `^I..HTH$` pattern precisely to keep `INDHTH` out.

## 5b. The filterable *Indicateurs* catalogue

`indicator_catalogue()` flattens all four families into one tidy frame —
`categorie, indicateur, vecteur, technologies, unite, year, value` — the same
four-facet shape ICEDD's *Indicateurs* page uses, so demand, emissions, heat and
capacity can be filtered together instead of navigated page by page. The page
renders it with chip filters per facet, a keyword box, sortable columns, a
sparkline per row and Δ% between the first and last horizon.

Facet assignment, following the Explorer's convention that `vecteur` is the
energy carrier and `technologies` the sub-category:

| Family | categorie | indicateur | vecteur | technologies |
|---|---|---|---|---|
| Demand | Consommation d'énergie | Consommation finale d'énergie | the carrier | the sector |
| Emissions | Émissions de CO2 | Émissions de gaz à effet de serre / Quantité de CO2 capturé | CO2 | the sector |
| Heat | Production d'énergie | Production de chaleur — {Résidentiel, Tertiaire, Industrie} | Chaleur | the heating technology |
| Power | Production d'énergie | Production wallonne d'électricité / Capacité installée de production d'électricité | Électricité | mode · technology |

Two properties the tests pin:

- **Every row is a leaf.** `demand_total` is the sum of the five sector tables,
  so it carries `in_catalogue=False`; any filtered selection can be summed
  without double counting. The one thing to know is that aviation kerosene *is*
  a row here while being excluded from the demand page's sector totals — the
  page says so.
- **The catalogue is derived from the charts**, not recomputed, so the two
  cannot drift apart.

The sparklines are inline SVG, not Plotly: ninety `Plotly.newPlot` calls for
ninety 130×26 thumbnails would cost a lot for a polyline, and an SVG path is not
a new dependency. The four charted pages still use Plotly, as the Sankeys do.

**What this page is not.** It reproduces the *shape* of the Explorer's
*Indicateurs* screenshot, not its contents — see §6 *Out of scope*.

## 5. Power fleet

Electricity output on any bus a generator can reach — the grid voltages plus the
sector carriers, because industrial autoproducer CHP never touches a grid bus
(it feeds `INDELC` directly) and a grid-only filter reports the whole Walloon
CHP fleet as producing nothing. Rooftop PV likewise splits its output between
`ELCHIG` (exported) and `RSDELC` (self-consumed); both count.

`indicator_power.csv` maps the process code to `(mode, technology)`; `mode` is
`chp`, `power` or `skip`. The `skip` rules drop grid transformers, pumped-hydro
turbining, battery discharge and the fuel-tech pass-throughs — all of them move
electricity that was already generated. Processes matching no rule are skipped
with a log line, so a new plant type shows up as a warning rather than silently
vanishing.

---

## 5c. Chart colours

The palette is the published one, not a look-alike: the swatches were sampled
from the legends of the December-2025 screenshots in
`figures_from_demande_haute_04-12-2025/`, and they are Plotly's
`qualitative.Plotly` for the first ten series of a chart followed by
`qualitative.D3` for the next ten. `indicator_pages.PALETTE` is that cycle, and
the total line is `#FF0000`, theirs too. A page of ours can therefore sit beside
a published one without the pair reading as two different reports.

**One deliberate difference: colours are stable per chart family, not per
chart.** The Explorer colours each chart independently — it sorts *that chart's*
series alphabetically and walks the palette — so `Electricity` is purple on the
industry chart, green on the tertiary one and blue on the residential one, and
`Industry` is red on the demand page but green on the emissions page. Here the
walk is done once per family and stored in `SERIES_COLORS[color_domain]`, so a
label keeps its colour on every chart that can show it.

The families are chosen so each one still reproduces its screenshot exactly: a
family's label universe *is* the series set of the published chart it comes from.
That is why the two sector families are separate — `Industry` is the second
demand sector but the third emitting sector, and both published charts are
matched. `carrier` reproduces the industry-demand chart swatch for swatch for its
first fifteen carriers; the four carriers that chart has no row for
(`Derived gas`, `Natural Gas`, `Kerosene`, `Solar`) take the tail of the cycle.

| `color_domain` | Charts | Universe |
|---|---|---|
| `carrier` | the five demand-by-carrier charts | `FUEL_ORDER`, alphabetically |
| `demand_sector` | *Demande énergétique générale* | `DEMAND_SECTORS`, alphabetically |
| `emission_sector` | the two emissions charts | `EMISSION_SECTORS`, alphabetically |
| `heat_technology` | the three heat charts | the published French labels' order |
| `power_technology` | the two power charts | none — row order into `PALETTE` |

Two rules keep it honest. A label the family does not name takes the first
unused `PALETTE` colour, so a chart never draws two bands the same colour even
when the map does not reach; and
`test_every_series_colour_comes_from_the_reference_palette` /
`test_published_charts_keep_their_published_swatches` pin both the cycle and the
three series sets the screenshots fix exactly.

`Cogeneration` is the one place the screenshots cannot be followed. Their
industry-heat chart has two rows (Chaudière, Cogénération) and gives cogeneration
the same red that `Direct electric heating` gets in their residential chart; ours
shows all four rows on the industry chart, so the two cannot share a colour and
cogeneration takes the next free one.

---

## 6. Reconciliation with the published December-2025 tables

2021 is the calibrated base year and is identical in both solves, so it is the
column that has to agree. Everything below is the 2021 deviation; the "max"
column spans 2021-2050 and mostly measures the two-day solve gap of §1.

| Table | 2021 | Notes |
|---|---|---|
| `demande_generale` | Residential, Tertiary, Transport **exact**; Agriculture +0.39 %; Industry +2.71 % | the two deviations are the rule differences below |
| `demande_residentiel` | **all rows exact** (Geothermal 0.09 %, a rounding artefact of the published 2-decimal value) | |
| `demande_tertiaire` | **all rows exact** | incl. the commercial-CHP heat share |
| `demande_industrie` | all rows exact except `Natural Gas Transport` +12.0 % | see below |
| `demande_transport` | all rows exact except `Kerosene` | reported here, absent there |
| `demande_agriculture` | all rows exact except `Wood` | reported here, absent there |
| `emission_sectors` | 0.14 – 0.87 % high | the N2O term, see below |
| `TIMES_heat_output` residential | **all rows exact** | |
| `TIMES_heat_output` tertiary | **all rows exact** | |
| `TIMES_heat_output` industry | total exact; the split differs | see below |
| `production_brute` / `capacite_installee` | 2021 exact for every technology | later years diverge, see below |

### Deliberate rule differences

1. **Agriculture wood (5.8 GWh, +0.39 % on the sector).** `AGRCPS` is consumed
   by `AGRHH00`; `demande_agriculture.csv` has no wood row at all. Counted here.
2. **Industrial `Natural Gas Transport` (+1063 GWh in 2021, +12 %).** `INDGAS`
   burnt in `CHPINDIPP00` / `CHPINDISG00` is dropped entirely by the published
   table, while the *same units'* `INDGMX`, `INDBLQ`, `INDCPS`, `INDBGS`,
   `INDLPG` and `INDHFO` all get the heat-share split. Treating one gas carrier
   differently from another in the same boiler is not a rule, so the heat share
   is applied uniformly here. **This is the largest single deviation and the one
   most worth a second opinion** — see §7.
3. **Aviation kerosene** is reported (8.3–8.7 TWh) but excluded from the
   transport total, so the totals still match.
4. **N2O in the GHG sum (0.14–0.87 %).** The published series is
   `CO2N + CO2P + 28·CH4` — dropping N2O reproduces `emission_sectors.csv` to
   four decimals for AGR, COM, ELC and RSD in 2021. That looks like an omission
   in their query rather than a convention, so N2O is kept here.
5. **Industry heat split.** The published `IND-HTH BOILER` (7043.4 GWh, 2021) is
   this table's `Boiler` (6421.0) + `District heat` (261.7) + the `INDHTH`
   double count (360.7). Totals agree once `INDHTH` is removed; the split is
   finer here, breaking out purchased network heat and electric process heat.
6. **Pumped hydro** is excluded from both generation and capacity.
   `capacite_installee.csv` counts its 1.31 GW as renewable capacity but
   `production_brute.csv` does not count its output, which cannot both be right.
7. **Renewable generation vs primary energy.** `production_brute.csv` reports
   wind and hydro as electricity but solar as the *solar resource into the PV*.
   Here everything is electricity generated. (In 2021 they coincide, because
   TIMES gives PV an input/output ratio of 1.)

### Not reconciled — assumed to be the solve gap

These are ~0 in 2021 and grow with the horizon, consistent with
`scen_corrige_251130_0312` vs `scen_corrige_251129_0112`:

- transport electricity (up to 5 %), natural gas (a constant 122 GWh in
  2035-2045) and oil products (a constant 57 GWh in 2040-2045);
- industrial hydrogen 2045 (3749 vs 2062 GWh) and industrial electricity 2045
  (15 892 vs 17 353 GWh);
- tertiary wood 2035-2040 and tertiary district heat 2045-2050;
- nuclear 2050: the `.vd` has `ETSTP_NUC-LWR-GEN3_NUC_N` at 2.4232 GW producing
  19 105 GWh, the published tables have 0.7114 GW producing 5 608 GWh — the same
  0.2936 ratio on both, i.e. a smaller plant in the other solve, not a different
  metric.

If any of these need to be closed rather than explained, the way to settle it is
to obtain `scen_corrige_251130_0312.vd` and re-run the comparison; the harness is
`tests/test_indicators.py::test_reproduces_icedd_2021`, which only needs its
reference dict swapped.

### Out of scope: the contents of the *Indicateurs* page

The *Indicateurs* screenshot (`Screenshot_2026-09-08_04-15-05.png`) is **not**
reproducible from a `.vd`. Its technology list — Gaz (CCGT), Gaz (OCGT), Gaz
(cycle Allam), Nucléaire (EPR), Nucléaire (SMR), Pile à combustible, Turbine
hydrogène — is PyPSA-Eur's carrier vocabulary, not TIMES'. `strategy_metrics_mapping.csv`
settles it: every one of its 107 rows names a PyPSA extraction output as its
source (`production_energy_df.csv`, `power_capacities.csv`,
`supply_energy_df.csv`, `load_temporal.csv`, `interconnection_capacities.csv`),
and not one names a TIMES file. That page is the ClimAct strategy view over the
solved networks.

What *is* reproduced is the page's **form** — the four-facet filter, the row per
series, the sparkline, the Δ% — over the TIMES indicators, as §5b describes. The
facet vocabulary is deliberately theirs, so the two tables read alike; the
indicator names are not, because the underlying quantities are different.

---

## 7. Decisions that may need revision

Listed loudest first. Each is a one-line change in a rule CSV unless noted.

1. **Industrial CHP gas (`INDGAS`).** §6.2. Applying the heat-share split adds
   ~1 TWh to industrial final energy in 2021 relative to the published table. If
   ICEDD's exclusion turns out to be deliberate, add `chp,zero` to the `INDGAS`
   row of `indicator_fuels.csv`.
2. **N2O in the GHG total.** §6.4. `ghg_emissions(..., gwp_n2o=0)` reproduces the
   published series; the default keeps it.
3. **`ELCBIO` labelled Biogas.** Its description is "Biomass (ELC)", but the
   only processes that burn it are the `*_BGS_*` biogas engines and the published
   tertiary table books it as biogas. Physically it may be solid biomass.
4. **Heat fed back into a cogeneration unit** (`<SEC>HET` with `chp: zero`) is
   not counted as the sector's final energy — it is the unit's own product
   recycled. Reproduces the published industrial `Heat` row exactly; drop the
   `chp` flag to count it at the heat share instead.
5. **`INDHTH` excluded from heat production.** §4. Removing the double count
   costs agreement with the published industry total (which contains it).
6. **`AGRCPS` counted as agricultural wood.** §6.1. 5.8 GWh, constant.
7. **Ambient and ground heat** (`RSDAHT`, `COMAHT`, `RSDGHT`, `COMGHT`) are not
   final energy. This matters more each horizon — 1.5 TWh of residential ambient
   heat by 2050 — and is the convention the published tables use.
8. **`INDHWT` (chaleur fatale) and `INDHTH` excluded from industrial demand.**
   Recovered internal heat, not purchased energy. Up to 1.1 TWh in 2030.
9. **Transport LPG.** The published table has round numbers (208, 153, 5) where
   the `.vd` has 207.98, 152.52, 5.39. Their figures appear to be hand-entered;
   the `.vd` values are used here.
10. **The power tables are the least validated of the four.** They are a bonus
    next to the three `[TIMES]` report pages, their definitions are stated in §5
    rather than reverse-engineered from a reference, and `production_brute.csv`
    is internally inconsistent (§6.6, §6.7). Read the 2021 column with
    confidence and the rest as this library's definition.

## 8. Adding a scenario or a carrier

- A **new carrier** in a demand sector: add a row to `indicator_fuels.csv`. The
  test `test_rule_tables_parse_and_are_self_consistent` rejects duplicates and
  labelled `exclude` rows.
- A **new plant type**: add a row to `indicator_power.csv` *above* the
  fallbacks. Until then it is skipped with an INFO log line naming it.
- A **new heating technology**: add a `technology` row to `indicator_heat.csv`
  above the final `.*`. Order matters; `ELCHP` must stay above `ELC`.
- A **new heat service commodity** (a new building type): extend the matching
  `segment` pattern. A commodity matching no segment is not heat, so the segment
  list is also the scope of the table.
