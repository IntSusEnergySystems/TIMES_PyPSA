"""Scenario indicator tables read straight out of a TIMES ``.vd``.

The soft-link path (``pipeline`` → ``qa`` → ``sankey_pages``) answers *where does
the energy go*. This module answers the four questions the Walloon scenario
report actually puts on a page:

* **Final energy demand** per sector and fuel (``energy_demand``)
* **Greenhouse-gas emissions** per sector (``ghg_emissions``) and the CO2 that
  is captured rather than emitted (``co2_capture``)
* **Heat production** per end use and heating technology (``heat_production``)
* **Electricity generation and capacity** per technology (``power_fleet``)

Nothing here goes through PyPSA: the numbers are the TIMES result, reported the
way the December-2025 ICEDD figures reported them. What each rule does and where
it deviates from those figures is written down in ``INDICATORS.md``; the rules
themselves live in ``data/indicator_fuels.csv``, ``data/indicator_heat.csv`` and
``data/indicator_power.csv`` so a mapping fix is a data edit, not a code change.

Energies are reported in **GWh**, emissions in **kt CO2eq**, capacity in **GW**.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from pathlib import Path

import pandas as pd

from times_pypsa.descriptions import resolve_data_dir
from times_pypsa.model import TimesAnnualFlows
from times_pypsa.pipeline import default_mappings_dir

logger = logging.getLogger(__name__)

# Exact, not PJ_TO_TWH * 1000: the ICEDD reference tables agree to four decimals
# and the rounded 0.277778 constant loses that at the third.
PJ_TO_GWH = 1000.0 / 3.6

VAR_FIN = "VAR_FIN"
VAR_FOUT = "VAR_FOUT"
VAR_CAP = "VAR_CAP"

#: Commodities that carry electricity out of a plant onto a bus. Used to split
#: CHP fuel between the electricity and the heat it produces. ``ELCHET``
#: ("Electricity - Heat") is deliberately absent — despite the prefix it is the
#: district-heat carrier, i.e. the heat side of the same split.
GRID_ELECTRICITY = ("ELCHIG", "ELCHIGG", "ELCMED", "ELCLOW")

#: Every carrier a generator can put its electricity on. Industrial autoproducer
#: cogeneration never touches a grid bus — it feeds ``INDELC`` directly — so a
#: grid-only filter would report the Walloon CHP fleet as producing nothing.
ELECTRICITY_OUTPUTS = GRID_ELECTRICITY + tuple(
    f"{sector}ELC" for sector in ("AGR", "COM", "IND", "RSD", "TRA")
)

#: Demand sectors, in the order the report shows them, with their display names.
DEMAND_SECTORS: dict[str, str] = {
    "AGR": "Agriculture",
    "IND": "Industry",
    "RSD": "Residential",
    "COM": "Tertiary",
    "TRA": "Transport",
}

#: Emission sectors, in report order. ``SUP`` is kept because the reference
#: tables list it (always zero for the Walloon model).
EMISSION_SECTORS: dict[str, str] = {
    "AGR": "Agriculture",
    "ELC": "Electricity",
    "IND": "Industry",
    "RSD": "Residential",
    "SUP": "Supply",
    "COM": "Tertiary",
    "TRA": "Transport",
}

#: AR5 GWP-100, the pair that reproduces the TIMES ``<SEC>GHG`` aggregate exactly.
GWP_CH4 = 28.0
GWP_N2O = 265.0

#: Fuels reported on their own row but left out of the sector total.
#: Aviation kerosene is an international bunker, not Walloon final energy.
TOTAL_EXCLUDES: frozenset[str] = frozenset({"Kerosene"})

#: Stack order for the fuel bars. Anything unlisted is appended alphabetically.
FUEL_ORDER: tuple[str, ...] = (
    "Electricity",
    "Gas mix",
    "Natural Gas",
    "Natural Gas Transport",
    "Derived gas",
    "Oil products",
    "LPG",
    "Kerosene",
    "Solid fuels",
    "Black liquor",
    "Wood",
    "Biogas",
    "Biofuel",
    "Waste",
    "Waste Renewable",
    "Hydrogen",
    "Heat",
    "Geothermal",
    "Solar",
)

#: Facet categories, in the vocabulary of the Explorer's *Indicateurs* page.
CAT_DEMAND = "Consommation d'énergie"
CAT_EMISSIONS = "Émissions de CO2"
CAT_PRODUCTION = "Production d'énergie"

_PROCESS_CODE_COL = "process_code"
_COMMODITY_CODE_COL = "commodity_code"


# --------------------------------------------------------------------------- #
# Rule tables
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class IndicatorRules:
    """The three CSV rule tables, parsed once."""

    fuels: pd.DataFrame
    heat: pd.DataFrame
    power: pd.DataFrame
    commodity_sets: dict[str, str] = field(default_factory=dict)

    def fuel_of(self, measures: tuple[str, ...]) -> dict[str, str]:
        sub = self.fuels[self.fuels["measure"].isin(measures)]
        return dict(zip(sub["commodity"], sub["fuel"]))

    @property
    def passthrough(self) -> list[str]:
        return list(self.fuels.loc[self.fuels["measure"] == "passthrough", "commodity"])

    def is_energy_commodity(self, code: str) -> bool:
        """True when the .vd dictionary marks the commodity as an energy carrier.

        Falls back to True when ``AllCommodities.csv`` is unavailable: the caller
        already restricts to VAR_FIn/VAR_FOut rows, so the worst case is that a
        non-energy output dilutes a CHP heat share rather than a crash.
        """
        if not self.commodity_sets:
            return True
        return "NRG" in self.commodity_sets.get(code, "")


def default_rules_dir() -> Path:
    """Directory holding the ``indicator_*.csv`` rule tables (repo ``data/``)."""
    return default_mappings_dir()


def _read_rule_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(f"Indicator rule table not found: {path}")
    df = pd.read_csv(path, comment="#", skip_blank_lines=True)
    return df.dropna(how="all")


def _load_commodity_sets(rules_dir: Path) -> dict[str, str]:
    data_dir = resolve_data_dir(rules_dir)
    if data_dir is None:
        return {}
    path = Path(data_dir) / "AllCommodities.csv"
    if not path.exists():
        return {}
    raw = pd.read_csv(path, sep=";", encoding="utf-8-sig", engine="python")
    if "Name" not in raw.columns or "Set" not in raw.columns:
        return {}
    raw = raw.drop_duplicates("Name")
    return {
        str(n).strip(): str(s or "")
        for n, s in zip(raw["Name"], raw["Set"])
        if str(n).strip()
    }


def load_indicator_rules(rules_dir: Path | str | None = None) -> IndicatorRules:
    """Parse the three indicator rule tables from ``rules_dir`` (default: repo data/)."""
    rules_dir = Path(rules_dir or default_rules_dir())
    fuels = _read_rule_csv(rules_dir / "indicator_fuels.csv")
    fuels["commodity"] = fuels["commodity"].astype(str).str.strip()
    fuels["measure"] = fuels["measure"].fillna("input").astype(str).str.strip()
    fuels["fuel"] = fuels["fuel"].fillna("").astype(str).str.strip()
    if "chp" not in fuels.columns:
        fuels["chp"] = ""
    fuels["chp"] = fuels["chp"].fillna("").astype(str).str.strip()

    heat = _read_rule_csv(rules_dir / "indicator_heat.csv")
    heat["kind"] = heat["kind"].astype(str).str.strip()
    heat["order"] = pd.to_numeric(heat["order"], errors="coerce").fillna(999)

    power = _read_rule_csv(rules_dir / "indicator_power.csv")
    power["renewable"] = pd.to_numeric(power["renewable"], errors="coerce").fillna(0).astype(int)
    power["order"] = pd.to_numeric(power["order"], errors="coerce").fillna(999)
    if "heat_to_sector" not in power.columns:
        power["heat_to_sector"] = ""
    power["heat_to_sector"] = power["heat_to_sector"].fillna("").astype(str).str.strip()

    return IndicatorRules(
        fuels=fuels,
        heat=heat,
        power=power,
        commodity_sets=_load_commodity_sets(rules_dir),
    )


def _first_match(patterns: pd.DataFrame, value: str, column: str, default: str = "") -> str:
    for _, row in patterns.iterrows():
        if re.match(str(row["pattern"]), value):
            return str(row[column])
    return default


# --------------------------------------------------------------------------- #
# Shared helpers over the flow table
# --------------------------------------------------------------------------- #


def _energy_rows(model: TimesAnnualFlows) -> pd.DataFrame:
    """VAR_FIn / VAR_FOut rows with normalised code columns."""
    flows = model.flows
    if flows.empty:
        return pd.DataFrame(
            columns=["year", "variable", "commodity", "process", "value", "sector", "process_type"]
        )
    df = flows[flows["variable"].str.upper().isin({VAR_FIN, VAR_FOUT})].copy()
    df["variable"] = df["variable"].str.upper()
    df["commodity"] = df[_COMMODITY_CODE_COL].astype(str).str.strip()
    df["process"] = df[_PROCESS_CODE_COL].astype(str).str.strip()
    df["value"] = pd.to_numeric(df["value"], errors="coerce").fillna(0.0)
    for col in ("sector", "process_type"):
        df[col] = df.get(col, "").fillna("").astype(str).str.strip()
    return df


def chp_heat_shares(model: TimesAnnualFlows, rules: IndicatorRules) -> pd.Series:
    """Share of each CHP process's output that is heat, per (process, year).

    A cogeneration unit burns one fuel stream for two products. Charging the
    whole stream to the sector that takes the heat would double-count it against
    the electricity the same unit sends to the grid, so the fuel is split in
    proportion to the two outputs. Returns a Series indexed by
    ``(process, year)``; processes with no electricity output get 1.0.
    """
    df = _energy_rows(model)
    if df.empty:
        return pd.Series(dtype=float)
    out = df[(df["variable"] == VAR_FOUT) & (df["process_type"] == "CHP")]
    out = out[out["commodity"].map(rules.is_energy_commodity)]
    if out.empty:
        return pd.Series(dtype=float)

    is_elec = out["commodity"].isin(GRID_ELECTRICITY) | out["commodity"].str.match(
        r"^(AGR|COM|ELC|IND|RSD|TRA)ELC$"
    )
    grouped = out.assign(kind=is_elec.map({True: "elec", False: "heat"})).groupby(
        ["process", "year", "kind"], observed=True
    )["value"].sum().unstack("kind")
    for col in ("elec", "heat"):
        if col not in grouped.columns:
            grouped[col] = 0.0
    grouped = grouped.fillna(0.0)
    total = grouped["elec"] + grouped["heat"]
    share = (grouped["heat"] / total).where(total > 0, 1.0)
    share.name = "heat_share"
    return share


def _fuel_tech_processes(model: TimesAnnualFlows) -> set[str]:
    """Processes whose only job is to hand a supply carrier to a sector.

    ``RSDOIL00`` converts ``OILDST`` into ``RSDOIL`` one-for-one. Counting both
    sides would double the sector's oil, so the fuel techs are metered on their
    output (the sector carrier) and skipped on their input.
    """
    flows = model.flows
    if flows.empty or "process" not in flows.columns:
        return set()
    desc = flows[["process_code", "process"]].drop_duplicates()
    mask = desc["process"].fillna("").astype(str).str.strip().str.lower().str.startswith("fuel tech")
    return set(desc.loc[mask, "process_code"].astype(str).str.strip())


# --------------------------------------------------------------------------- #
# 1. Final energy demand
# --------------------------------------------------------------------------- #


def energy_demand(
    model: TimesAnnualFlows,
    rules: IndicatorRules | None = None,
) -> pd.DataFrame:
    """Final energy delivered to each demand sector, by fuel and year.

    Returns tidy rows ``[sector, sector_name, fuel, year, gwh]``.

    The measurement point is the **sector-owned carrier** (``RSDGMX``,
    ``INDELC``, …), summed over every process that consumes it, and the row is
    booked to the sector of that *consuming* process. Metering there rather than
    on the supply carrier is what keeps a heat pump's electricity in the
    electricity row and puts home EV charging — which draws ``RSDELC`` from a
    transport process — in Transport rather than Residential.

    Three weights are applied on top:

    * rooftop PV and battery storage (process types ``ELE`` / ``STG`` / ``STS``)
      draw a sector carrier without consuming final energy → weight 0;
    * cogeneration is weighted by :func:`chp_heat_shares`, so only the fuel that
      became heat is charged to the sector;
    * the fuel-tech pass-through processes are skipped (see
      :func:`_fuel_tech_processes`).

    Blended road fuels (``TRADST``, ``TRAGSL``, ``TRAETH``) are resolved into
    their upstream mix so the fossil and biofuel shares land in separate rows.
    """
    rules = rules or load_indicator_rules()
    df = _energy_rows(model)
    if df.empty:
        return pd.DataFrame(columns=["sector", "sector_name", "fuel", "year", "gwh"])

    direct = rules.fuel_of(("input",))
    upstream = rules.fuel_of(("input", "upstream", "chp_fuel"))
    passthrough = set(rules.passthrough)
    excluded = set(rules.fuels.loc[rules.fuels["measure"] == "exclude", "commodity"])

    owner = dict(zip(rules.fuels["commodity"], rules.fuels["sector"]))
    no_chp = set(rules.fuels.loc[rules.fuels["chp"] == "zero", "commodity"])

    fin = df[df["variable"] == VAR_FIN].copy()
    fin = fin[~fin["commodity"].isin(excluded)]
    fin = fin[~fin["process"].isin(_fuel_tech_processes(model))]
    # A process the mapping leaves *unclassified* still consumes a carrier that
    # belongs to exactly one sector, so fall back to the carrier's owner rather
    # than dropping the flow: `mapping_processes.csv` has a blank Sector cell for
    # some newer devices (RW4FGMXN3 and friends, 0.5 TWh of residential gas).
    # A process classified into a *non-demand* sector is a different matter and
    # stays out: BBLQH2G110 is a SUP gasifier that turns industrial black liquor
    # into synthetic hydrogen, so its feedstock is a conversion input, not
    # industrial final energy.
    blank = fin["sector"].eq("")
    fin.loc[blank, "sector"] = fin.loc[blank, "commodity"].map(owner).fillna("")
    fin = fin[fin["sector"].isin(DEMAND_SECTORS)]

    # Weights: zero for non-consuming draws, heat share for cogeneration.
    weight = pd.Series(1.0, index=fin.index)
    weight[fin["process_type"].isin({"ELE", "STG", "STS"})] = 0.0
    shares = chp_heat_shares(model, rules)
    chp = fin["process_type"] == "CHP"
    if chp.any():
        if not shares.empty:
            keys = pd.MultiIndex.from_arrays([fin.loc[chp, "process"], fin.loc[chp, "year"]])
            weight[chp] = shares.reindex(keys).fillna(1.0).to_numpy()
        weight[chp & fin["commodity"].isin(no_chp)] = 0.0
    fin["weighted"] = fin["value"] * weight

    records: list[pd.DataFrame] = []

    plain = fin[fin["commodity"].isin(direct)].copy()
    if not plain.empty:
        plain["fuel"] = plain["commodity"].map(direct)
        records.append(plain[["sector", "fuel", "year", "weighted"]])

    blend = fin[fin["commodity"].isin(passthrough)]
    if not blend.empty:
        mix = _passthrough_mix(df, passthrough, upstream)
        for commodity, grp in blend.groupby("commodity", observed=True):
            shares_by_year = mix.get(commodity)
            if shares_by_year is None:
                logger.warning(
                    "Pass-through commodity %s has no producing process; "
                    "its consumption is dropped from the demand table.",
                    commodity,
                )
                continue
            merged = grp.merge(shares_by_year, on="year", how="left")
            merged = merged.dropna(subset=["fuel"])
            merged["weighted"] = merged["weighted"] * merged["share"]
            records.append(merged[["sector", "fuel", "year", "weighted"]])

    records.append(_output_measured_fuels(df, rules))
    records.append(_chp_heat_to_sector(df, rules, model))

    tidy = pd.concat([r for r in records if not r.empty], ignore_index=True)
    tidy = tidy.groupby(["sector", "fuel", "year"], as_index=False, observed=True)["weighted"].sum()
    tidy = tidy.rename(columns={"weighted": "gwh"})
    tidy["gwh"] = tidy["gwh"] * PJ_TO_GWH
    tidy["sector_name"] = tidy["sector"].map(DEMAND_SECTORS)
    tidy = tidy[tidy["gwh"].abs() > 1e-9]
    return tidy[["sector", "sector_name", "fuel", "year", "gwh"]].sort_values(
        ["sector", "fuel", "year"]
    ).reset_index(drop=True)


def _passthrough_mix(
    df: pd.DataFrame,
    passthrough: set[str],
    fuel_of: dict[str, str],
) -> dict[str, pd.DataFrame]:
    """For each blended carrier, the per-year fuel shares of its producers' inputs."""
    out: dict[str, pd.DataFrame] = {}
    producers = df[(df["variable"] == VAR_FOUT) & df["commodity"].isin(passthrough)]
    for commodity, grp in producers.groupby("commodity", observed=True):
        procs = set(grp["process"])
        inputs = df[(df["variable"] == VAR_FIN) & df["process"].isin(procs)].copy()
        inputs["fuel"] = inputs["commodity"].map(fuel_of)
        inputs = inputs.dropna(subset=["fuel"])
        if inputs.empty:
            continue
        per_year = inputs.groupby(["year", "fuel"], as_index=False, observed=True)["value"].sum()
        totals = per_year.groupby("year", observed=True)["value"].transform("sum")
        per_year["share"] = (per_year["value"] / totals).where(totals != 0, 0.0)
        out[commodity] = per_year[["year", "fuel", "share"]]
    return out


def _output_measured_fuels(df: pd.DataFrame, rules: IndicatorRules) -> pd.DataFrame:
    """Carriers metered on production because nothing consumes them in the .vd."""
    rows = rules.fuels[rules.fuels["measure"] == "output"]
    if rows.empty:
        return pd.DataFrame(columns=["sector", "fuel", "year", "weighted"])
    lookup = dict(zip(rows["commodity"], zip(rows["sector"], rows["fuel"])))
    sub = df[(df["variable"] == VAR_FOUT) & df["commodity"].isin(lookup)].copy()
    if sub.empty:
        return pd.DataFrame(columns=["sector", "fuel", "year", "weighted"])
    sub["sector"] = sub["commodity"].map(lambda c: lookup[c][0])
    sub["fuel"] = sub["commodity"].map(lambda c: lookup[c][1])
    sub["weighted"] = sub["value"]
    return sub[["sector", "fuel", "year", "weighted"]]


def _chp_heat_to_sector(
    df: pd.DataFrame,
    rules: IndicatorRules,
    model: TimesAnnualFlows,
) -> pd.DataFrame:
    """Fuel of an electricity-sector CHP whose heat serves a demand sector.

    Commercial cogeneration sits in the TIMES ``ELC`` sector and burns ``ELC*``
    carriers, so the sector-carrier rule above never sees it — yet its heat is
    delivered to tertiary buildings. The ``heat_to_sector`` column of
    ``indicator_power.csv`` names the sector that gets the heat-allocated share.
    """
    targets = rules.power[rules.power["heat_to_sector"] != ""]
    if targets.empty:
        return pd.DataFrame(columns=["sector", "fuel", "year", "weighted"])
    chp_fuels = rules.fuel_of(("chp_fuel",))
    if not chp_fuels:
        return pd.DataFrame(columns=["sector", "fuel", "year", "weighted"])

    fin = df[(df["variable"] == VAR_FIN) & df["commodity"].isin(chp_fuels)].copy()
    if fin.empty:
        return pd.DataFrame(columns=["sector", "fuel", "year", "weighted"])
    fin["sector"] = fin["process"].map(lambda p: _first_match(targets, p, "heat_to_sector"))
    fin = fin[fin["sector"] != ""]
    if fin.empty:
        return pd.DataFrame(columns=["sector", "fuel", "year", "weighted"])

    shares = chp_heat_shares(model, rules)
    keys = pd.MultiIndex.from_arrays([fin["process"], fin["year"]])
    fin["weighted"] = fin["value"] * shares.reindex(keys).fillna(1.0).to_numpy()
    fin["fuel"] = fin["commodity"].map(chp_fuels)
    return fin[["sector", "fuel", "year", "weighted"]]


# --------------------------------------------------------------------------- #
# 2. Emissions
# --------------------------------------------------------------------------- #

_EMISSION_RE = re.compile(r"^(AGR|COM|ELC|IND|RSD|SUP|TRA)(CO2N|CO2P|CH4|N2O)$")
_CAPTURE_RE = re.compile(r"^(AGR|COM|ELC|IND|RSD|SUP|TRA)CO2c$")


def ghg_emissions(
    model: TimesAnnualFlows,
    *,
    gwp_ch4: float = GWP_CH4,
    gwp_n2o: float = GWP_N2O,
) -> pd.DataFrame:
    """Greenhouse-gas emissions per sector and year, in kt CO2eq.

    Summed from the **per-process** emission rows, not from the ``<SEC>GHG``
    aggregate the model also writes. The two agree wherever nothing is captured;
    where CCS runs they do not, because ``<SEC>GHG`` counts the gross stack
    emission and lets the captured tonne leave separately through ``<SEC>CO2c``.
    Building the total from the gases makes the captured CO2 an explicit,
    separate series (:func:`co2_capture`) instead of a silent inflation of the
    industry and electricity rows from 2035 on.

    Biogenic CO2 (``<SEC>CO2b``) is excluded, per the usual inventory
    convention. The default GWPs are AR5 GWP-100.
    """
    flows = model.flows
    if flows.empty:
        return pd.DataFrame(columns=["sector", "sector_name", "year", "ktco2eq"])
    df = flows[flows["variable"].str.upper() == VAR_FOUT].copy()
    df["commodity"] = df[_COMMODITY_CODE_COL].astype(str).str.strip()
    df["process"] = df[_PROCESS_CODE_COL].astype(str).str.strip()
    # Region aggregate rows ("-") restate what the process rows already carry.
    df = df[df["process"] != "-"]
    match = df["commodity"].map(lambda c: _EMISSION_RE.match(c))
    df = df[match.notna()]
    if df.empty:
        return pd.DataFrame(columns=["sector", "sector_name", "year", "ktco2eq"])
    df["sector"] = df["commodity"].str[:3]
    df["gas"] = df["commodity"].str[3:]
    weights = {"CO2N": 1.0, "CO2P": 1.0, "CH4": float(gwp_ch4), "N2O": float(gwp_n2o)}
    df["ktco2eq"] = pd.to_numeric(df["value"], errors="coerce").fillna(0.0) * df["gas"].map(weights)

    out = df.groupby(["sector", "year"], as_index=False, observed=True)["ktco2eq"].sum()
    out["sector_name"] = out["sector"].map(EMISSION_SECTORS).fillna(out["sector"])
    return out[["sector", "sector_name", "year", "ktco2eq"]].sort_values(["sector", "year"]).reset_index(
        drop=True
    )


def co2_capture(model: TimesAnnualFlows) -> pd.DataFrame:
    """CO2 routed to capture rather than the atmosphere, per sector and year (kt).

    Reported positive; the report chart plots it below the axis.
    """
    flows = model.flows
    if flows.empty:
        return pd.DataFrame(columns=["sector", "sector_name", "year", "kt"])
    df = flows[flows["variable"].str.upper() == VAR_FOUT].copy()
    df["commodity"] = df[_COMMODITY_CODE_COL].astype(str).str.strip()
    df["process"] = df[_PROCESS_CODE_COL].astype(str).str.strip()
    df = df[df["process"] != "-"]
    df = df[df["commodity"].map(lambda c: bool(_CAPTURE_RE.match(c)))]
    if df.empty:
        return pd.DataFrame(columns=["sector", "sector_name", "year", "kt"])
    df["sector"] = df["commodity"].str[:3]
    df["kt"] = pd.to_numeric(df["value"], errors="coerce").fillna(0.0)
    out = df.groupby(["sector", "year"], as_index=False, observed=True)["kt"].sum()
    out["sector_name"] = out["sector"].map(EMISSION_SECTORS).fillna(out["sector"])
    return out[["sector", "sector_name", "year", "kt"]].sort_values(["sector", "year"]).reset_index(
        drop=True
    )


# --------------------------------------------------------------------------- #
# 3. Heat production
# --------------------------------------------------------------------------- #


def heat_production(
    model: TimesAnnualFlows,
    rules: IndicatorRules | None = None,
) -> pd.DataFrame:
    """Useful heat delivered per end use and heating technology, in GWh.

    Metered on the *output* of the heating device (the heat service commodity),
    so a heat pump shows the heat it delivers rather than the electricity it
    drew — which is the number a heating-technology chart needs, and the reason
    this table cannot be derived from :func:`energy_demand`.

    Returns tidy rows ``[segment, segment_order, technology, technology_order,
    year, gwh]``.
    """
    rules = rules or load_indicator_rules()
    df = _energy_rows(model)
    if df.empty:
        return pd.DataFrame(
            columns=["segment", "segment_order", "technology", "technology_order", "year", "gwh"]
        )

    segments = rules.heat[rules.heat["kind"] == "segment"]
    techs = rules.heat[rules.heat["kind"] == "technology"]

    out = df[df["variable"] == VAR_FOUT].copy()
    seg_of = {c: _first_match(segments, c, "label") for c in out["commodity"].unique()}
    out["segment"] = out["commodity"].map(seg_of)
    out = out[out["segment"] != ""]
    if out.empty:
        return pd.DataFrame(
            columns=["segment", "segment_order", "technology", "technology_order", "year", "gwh"]
        )
    tech_of = {p: _first_match(techs, p, "label", "Boiler") for p in out["process"].unique()}
    out["technology"] = out["process"].map(tech_of)
    out = out[out["technology"] != "skip"]
    if out.empty:
        return pd.DataFrame(
            columns=["segment", "segment_order", "technology", "technology_order", "year", "gwh"]
        )

    seg_order = {
        str(r["label"]): float(r["order"]) for _, r in segments.iterrows()
    }
    tech_order = {str(r["label"]): float(r["order"]) for _, r in techs.iterrows()}

    tidy = out.groupby(["segment", "technology", "year"], as_index=False, observed=True)["value"].sum()
    tidy["gwh"] = tidy["value"] * PJ_TO_GWH
    tidy["segment_order"] = tidy["segment"].map(seg_order).fillna(999)
    tidy["technology_order"] = tidy["technology"].map(tech_order).fillna(999)
    tidy = tidy[tidy["gwh"].abs() > 1e-9]
    return tidy[
        ["segment", "segment_order", "technology", "technology_order", "year", "gwh"]
    ].sort_values(["segment_order", "technology_order", "year"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# 4. Electricity generation and capacity
# --------------------------------------------------------------------------- #


def power_fleet(
    model: TimesAnnualFlows,
    rules: IndicatorRules | None = None,
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Grid electricity (GWh) and installed capacity (GW) per technology.

    Returns ``(generation, capacity)``, both tidy on
    ``[mode, technology, renewable, order, year, value]``. ``mode`` is ``chp``
    or ``power`` (cogeneration vs power-only), matching how the scenario report
    splits the fleet.

    Only processes that put electricity on a bus are classified, and only those
    ``indicator_power.csv`` names: transformers, pumped-hydro turbining and
    battery discharge move electricity that was already generated and are
    dropped by the ``skip`` rules rather than counted twice, while the fuel-tech
    pass-throughs match no rule at all.
    """
    rules = rules or load_indicator_rules()
    df = _energy_rows(model)
    empty = pd.DataFrame(columns=["mode", "technology", "renewable", "order", "year", "value"])
    if df.empty:
        return empty.copy(), empty.copy()

    gen = df[(df["variable"] == VAR_FOUT) & df["commodity"].isin(ELECTRICITY_OUTPUTS)].copy()
    if gen.empty:
        return empty.copy(), empty.copy()

    classified = {p: _classify_power(rules.power, p) for p in gen["process"].unique()}
    keep = {p: c for p, c in classified.items() if c is not None and c["mode"] != "skip"}

    gen = gen[gen["process"].isin(keep)]
    if gen.empty:
        return empty.copy(), empty.copy()
    for key in ("mode", "technology", "renewable", "order"):
        gen[key] = gen["process"].map(lambda p, k=key: keep[p][k])
    generation = gen.groupby(
        ["mode", "technology", "renewable", "order", "year"], as_index=False, observed=True
    )["value"].sum()
    generation["value"] = generation["value"] * PJ_TO_GWH

    flows = model.flows
    cap = flows[flows["variable"].str.upper() == VAR_CAP].copy()
    cap["process"] = cap[_PROCESS_CODE_COL].astype(str).str.strip()
    cap = cap[cap["process"].isin(keep)]
    if cap.empty:
        capacity = empty.copy()
    else:
        cap["value"] = pd.to_numeric(cap["value"], errors="coerce").fillna(0.0)
        for key in ("mode", "technology", "renewable", "order"):
            cap[key] = cap["process"].map(lambda p, k=key: keep[p][k])
        capacity = cap.groupby(
            ["mode", "technology", "renewable", "order", "year"], as_index=False, observed=True
        )["value"].sum()

    return _tidy_fleet(generation), _tidy_fleet(capacity)


def _classify_power(patterns: pd.DataFrame, process: str) -> dict | None:
    for _, row in patterns.iterrows():
        if re.match(str(row["pattern"]), process):
            return {
                "mode": str(row["mode"]),
                "technology": str(row["technology"]),
                "renewable": int(row["renewable"]),
                "order": float(row["order"]),
            }
    logger.info("Power process %s matches no indicator_power.csv rule; skipped.", process)
    return None


def _tidy_fleet(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df
    df = df[df["value"].abs() > 1e-12]
    return df.sort_values(["mode", "order", "technology", "year"]).reset_index(drop=True)


# --------------------------------------------------------------------------- #
# Table assembly for the report pages
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class IndicatorTable:
    """One chart-plus-table block of the report."""

    key: str
    group: str
    title: str
    subtitle: str
    unit: str
    row_label: str
    frame: pd.DataFrame  # index = row label, columns = years
    total: pd.Series | None = None

    # Facets for the flat, filterable catalogue (see `indicator_catalogue`).
    # Every series is addressed by four facets — categorie / indicateur /
    # vecteur / technologies — the way the Explorer's *Indicateurs* page does.
    # The table's rows fill one of `vecteur` or `technologies` (`row_facet`);
    # `other_facet` is the constant value of the other one.
    categorie: str = ""
    indicateur: str = ""
    row_facet: str = "technologies"
    other_facet: str = ""
    #: False for a table that is the sum of other tables, so the catalogue stays
    #: a set of leaf series that can be filtered and summed without double
    #: counting.
    in_catalogue: bool = True

    @property
    def years(self) -> list[int]:
        return [int(c) for c in self.frame.columns]

    def to_csv_frame(self) -> pd.DataFrame:
        """Wide frame with the total row on top, matching the report tables."""
        if self.total is None:
            return self.frame
        total = self.total.reindex(self.frame.columns)
        return pd.concat([total.to_frame("Total").T, self.frame])


def _wide(tidy: pd.DataFrame, index: str, value: str, order: list[str] | None = None) -> pd.DataFrame:
    if tidy.empty:
        return pd.DataFrame()
    wide = tidy.pivot_table(index=index, columns="year", values=value, aggfunc="sum", observed=True)
    wide = wide.reindex(sorted(wide.columns), axis=1)
    if order:
        ranked = [r for r in order if r in wide.index]
        rest = sorted(r for r in wide.index if r not in set(order))
        wide = wide.reindex(ranked + rest)
    return wide.fillna(0.0)


def build_indicator_tables(
    model: TimesAnnualFlows,
    rules: IndicatorRules | None = None,
    *,
    years: list[int] | None = None,
) -> list[IndicatorTable]:
    """Every table the report pages render, in page order."""
    rules = rules or load_indicator_rules()
    tables: list[IndicatorTable] = []

    demand = energy_demand(model, rules)
    if years is not None:
        demand = demand[demand["year"].isin(years)]

    # --- Demand: whole system, then one table per sector ---------------------
    counted = demand[~demand["fuel"].isin(TOTAL_EXCLUDES)]
    by_sector = _wide(counted, "sector_name", "gwh", list(DEMAND_SECTORS.values()))
    if not by_sector.empty:
        tables.append(
            IndicatorTable(
                key="demand_total",
                group="demand",
                title="Demande énergétique générale",
                subtitle=(
                    "Final energy delivered to the five demand sectors. Aviation "
                    "kerosene is reported under Transport but excluded from the "
                    "total (international bunkers)."
                ),
                unit="GWh",
                row_label="Secteur",
                frame=by_sector,
                total=by_sector.sum(),
                categorie=CAT_DEMAND,
                indicateur="Consommation finale d'énergie",
                row_facet="technologies",
                other_facet="Tous vecteurs",
                # The sum of the five sector tables; listing it too would double
                # count every filtered selection.
                in_catalogue=False,
            )
        )
    for code, name in DEMAND_SECTORS.items():
        sub = demand[demand["sector"] == code]
        frame = _wide(sub, "fuel", "gwh", list(FUEL_ORDER))
        if frame.empty:
            continue
        counted_rows = [f for f in frame.index if f not in TOTAL_EXCLUDES]
        tables.append(
            IndicatorTable(
                key=f"demand_{code.lower()}",
                group="demand",
                title=f"Demande énergétique — {name}",
                subtitle=f"Final energy by carrier, {name.lower()} sector.",
                unit="GWh",
                row_label="Vecteur",
                frame=frame,
                total=frame.loc[counted_rows].sum(),
                categorie=CAT_DEMAND,
                indicateur="Consommation finale d'énergie",
                row_facet="vecteur",
                other_facet=name,
            )
        )

    # --- Emissions -----------------------------------------------------------
    ghg = ghg_emissions(model)
    if years is not None:
        ghg = ghg[ghg["year"].isin(years)]
    frame = _wide(ghg, "sector_name", "ktco2eq", list(EMISSION_SECTORS.values()))
    if not frame.empty:
        tables.append(
            IndicatorTable(
                key="emissions_sectors",
                group="emissions",
                title="Émissions de GES par secteur",
                subtitle=(
                    "CO2 (combustion and process) plus CH4 and N2O at AR5 GWP-100, "
                    "summed from the per-process emission flows. Biogenic CO2 is "
                    "excluded; captured CO2 is shown separately below."
                ),
                unit="ktCO2eq",
                row_label="Secteur",
                frame=frame,
                total=frame.sum(),
                categorie=CAT_EMISSIONS,
                indicateur="Émissions de gaz à effet de serre",
                row_facet="technologies",
                other_facet="CO2",
            )
        )
    capture = co2_capture(model)
    if years is not None:
        capture = capture[capture["year"].isin(years)]
    cframe = _wide(capture, "sector_name", "kt", list(EMISSION_SECTORS.values()))
    if not cframe.empty:
        tables.append(
            IndicatorTable(
                key="emissions_capture",
                group="emissions",
                title="CO2 capturé",
                subtitle="CO2 routed to capture instead of the atmosphere.",
                unit="ktCO2",
                row_label="Secteur",
                frame=cframe,
                total=cframe.sum(),
                categorie=CAT_EMISSIONS,
                indicateur="Quantité de CO2 capturé",
                row_facet="technologies",
                other_facet="CO2",
            )
        )

    # --- Heat ----------------------------------------------------------------
    heat = heat_production(model, rules)
    if years is not None:
        heat = heat[heat["year"].isin(years)]
    for slug, label, keys in (
        ("residential", "Résidentiel", ("Residential space heating", "Residential water heating")),
        ("tertiary", "Tertiaire", ("Tertiary space heating", "Tertiary water heating")),
        ("industry", "Industrie", ("Industry process heat",)),
    ):
        sub = heat[heat["segment"].isin(keys)]
        if sub.empty:
            continue
        order = (
            sub.sort_values("technology_order")["technology"].drop_duplicates().tolist()
        )
        frame = _wide(sub, "technology", "gwh", order)
        tables.append(
            IndicatorTable(
                key=f"heat_{slug}",
                group="heat",
                title=f"Production de chaleur — {label}",
                subtitle=(
                    "Useful heat delivered, metered on the output of the heating "
                    "device (so a heat pump shows heat, not electricity)."
                ),
                unit="GWh",
                row_label="Technologie",
                frame=frame,
                total=frame.sum(),
                categorie=CAT_PRODUCTION,
                indicateur=f"Production de chaleur — {label}",
                row_facet="technologies",
                other_facet="Chaleur",
            )
        )

    # --- Power ---------------------------------------------------------------
    generation, capacity = power_fleet(model, rules)
    for tidy, key, title, unit, subtitle, indicateur in (
        (
            generation,
            "power_generation",
            "Production électrique brute",
            "GWh",
            "Electricity sent to a grid bus, by technology. Cogeneration is "
            "separated from power-only plant.",
            "Production wallonne d'électricité",
        ),
        (
            capacity,
            "power_capacity",
            "Capacité électrique installée",
            "GW",
            "Installed electrical capacity of the same fleet.",
            "Capacité installée de production d'électricité",
        ),
    ):
        if years is not None and not tidy.empty:
            tidy = tidy[tidy["year"].isin(years)]
        if tidy.empty:
            continue
        labelled = tidy.assign(
            row=tidy["mode"].map({"chp": "Cogénération", "power": "Production seule"})
            + " · "
            + tidy["technology"]
        )
        order = (
            labelled.sort_values(["mode", "order"])["row"].drop_duplicates().tolist()
        )
        frame = _wide(labelled, "row", "value", order)
        tables.append(
            IndicatorTable(
                key=key,
                group="power",
                title=title,
                subtitle=subtitle,
                unit=unit,
                row_label="Technologie",
                frame=frame,
                total=frame.sum(),
                categorie=CAT_PRODUCTION,
                indicateur=indicateur,
                row_facet="technologies",
                other_facet="Électricité",
            )
        )

    return tables


# --------------------------------------------------------------------------- #
# Flat, faceted catalogue
# --------------------------------------------------------------------------- #

#: Column order of :func:`indicator_catalogue`, matching the Explorer's
#: *Indicateurs* table so the two can be read side by side.
CATALOGUE_FACETS: tuple[str, ...] = ("categorie", "indicateur", "vecteur", "technologies")


def indicator_catalogue(
    model: TimesAnnualFlows,
    rules: IndicatorRules | None = None,
    *,
    years: list[int] | None = None,
    tables: list[IndicatorTable] | None = None,
) -> pd.DataFrame:
    """Every indicator series as one flat, filterable row per (facets, year).

    Columns: ``categorie, indicateur, vecteur, technologies, unite, year, value``
    — the shape the Explorer's *Indicateurs* page uses, so one table can carry
    demand, emissions, heat and capacity together and be filtered on four
    facets instead of navigated page by page.

    Derived from :func:`build_indicator_tables` rather than recomputed, so the
    catalogue and the charts cannot disagree. Tables that are the sum of other
    tables carry ``in_catalogue=False`` and are left out, which keeps every row a
    leaf series: any filtered subset can be summed without double counting.
    """
    if tables is None:
        tables = build_indicator_tables(model, rules or load_indicator_rules(), years=years)

    records: list[pd.DataFrame] = []
    for table in tables:
        if not table.in_catalogue or table.frame.empty:
            continue
        long = table.frame.stack()
        if long.empty:
            continue
        long = long.rename("value").reset_index()
        long.columns = ["row", "year", "value"]
        long["categorie"] = table.categorie
        long["indicateur"] = table.indicateur
        long[table.row_facet] = long["row"]
        other = "technologies" if table.row_facet == "vecteur" else "vecteur"
        long[other] = table.other_facet
        long["unite"] = table.unit
        records.append(long[[*CATALOGUE_FACETS, "unite", "year", "value"]])

    if not records:
        return pd.DataFrame(columns=[*CATALOGUE_FACETS, "unite", "year", "value"])

    out = pd.concat(records, ignore_index=True)
    out["year"] = out["year"].astype(int)
    out["value"] = pd.to_numeric(out["value"], errors="coerce").fillna(0.0)
    return out.sort_values([*CATALOGUE_FACETS, "year"]).reset_index(drop=True)


def catalogue_series(catalogue: pd.DataFrame) -> tuple[list[int], list[dict]]:
    """``(years, series)`` — the catalogue pivoted one row per series.

    ``series`` entries are ``{categorie, indicateur, vecteur, technologies,
    unite, values}`` with ``values`` aligned to ``years``. This is what the
    filterable page renders and what a caller wanting "one row per indicator"
    should use; the tidy frame is easier to group, this is easier to display.
    """
    if catalogue.empty:
        return [], []
    years = sorted(int(y) for y in catalogue["year"].unique())
    wide = catalogue.pivot_table(
        index=[*CATALOGUE_FACETS, "unite"],
        columns="year",
        values="value",
        aggfunc="sum",
        observed=True,
    ).reindex(columns=years)
    series = []
    for keys, row in wide.iterrows():
        entry = dict(zip([*CATALOGUE_FACETS, "unite"], keys))
        entry["values"] = [None if pd.isna(v) else float(v) for v in row.tolist()]
        series.append(entry)
    return years, series
