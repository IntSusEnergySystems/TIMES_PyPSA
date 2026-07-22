"""Aggregation levels for TIMES Sankey / QA views."""

from __future__ import annotations

import logging
import re

import pandas as pd

from times_pypsa.pipeline import net_bidirectional_links

logger = logging.getLogger(__name__)

# Columns shared between mapping CSVs that must not be used as agg levels.
EXCLUDED_AGG_COLUMNS = frozenset(
    {
        "Process",
        "Technology (Process)",
        "TIMES commodity",
        "Description",
        "Unit",
        "Set",
        "Type",
        "Activity unit",
        "Capacity unit",
        "Primary commodity group",
        "Time slice level",
        "Vintage",
        "PyPSA technology",
        "Upstream commodity",
        "Upstream process",
        "Comment",
    }
)

LEVEL_ALIASES: dict[str, str] = {
    "L0": "Sector",
    "L1": "Aggregation Level 1",
    "mapping": "Aggregation Level 2",
}

# Sector codes as stored in mapping_processes.csv (no invented display names)
SECTOR_LABELS = {
    "AGR": "AGR",
    "COM": "COM",
    "ELC": "ELC",
    "IMP": "IMP",
    "IND": "IND",
    "RSD": "RSD",
    "SUP": "SUP",
    "TRA": "TRA",
}


def shared_aggregation_columns(
    processes_df: pd.DataFrame, commodities_df: pd.DataFrame
) -> list[str]:
    """Return column names present in BOTH dataframes usable as aggregation levels."""
    shared = set(processes_df.columns) & set(commodities_df.columns)
    return sorted(c for c in shared if c not in EXCLUDED_AGG_COLUMNS)


def proc_agg_col(level: str) -> str:
    return f"proc_agg__{level}"


def com_agg_col(level: str) -> str:
    return f"com_agg__{level}"


def _resolve_agg_level(level: str) -> str:
    if level == "L2":
        return "L2"
    return LEVEL_ALIASES.get(level, level)


def _available_agg_levels(flows: pd.DataFrame) -> list[str]:
    return sorted(
        col[len("proc_agg__") :]
        for col in flows.columns
        if col.startswith("proc_agg__")
    )


def _nonempty_labels(series: pd.Series) -> pd.Series:
    labels = series.fillna("").astype(str).str.strip()
    return labels.where(~labels.str.lower().eq("nan") & labels.ne(""), other="")


def _clean_text(value: object) -> str:
    text = str(value or "").strip()
    if not text or text.lower() == "nan":
        return ""
    return text


def identity_label(*candidates: object) -> str:
    """
    First non-empty candidate (description, code, …).

    Never returns ``Unknown`` — labels must come from TIMES names/codes.
    """
    for candidate in candidates:
        text = _clean_text(candidate)
        if text:
            return text
    return "Unspecified"


_OVERVIEW_CARRIER_FAMILIES = frozenset(
    {
        "Electricity",
        "Gas",
        "Heat",
        "Oil products",
        "Hydrogen",
        "Coal & solids",
        "Biomass & biofuels",
        "Nuclear fuel",
    }
)


def infer_overview_commodity_label(
    *,
    description: str = "",
    code: str = "",
    carrier: str = "",
    cluster: str = "",
) -> str:
    """Heuristic carrier-family label used when ``sankey_overview`` is missing."""
    text = f"{carrier} {cluster} {description} {code}".lower()
    code_u = _clean_text(code).upper()
    code_l = code_u.lower()
    carrier_l = _clean_text(carrier).lower()

    # Nuclear fuel is not electricity — keep it out of Electricity hubs.
    if code_u == "NUCRSV" or "uranium" in text:
        return "Nuclear fuel"

    if code_u == "TRAETH" or "ethanol" in text:
        return "Biomass & biofuels"
    if "batelc" in code_l or "battery" in text:
        return "Electricity"
    if any(k in text for k in ("hydrogen", "h2 for", "h2 ")) or code_u.startswith("H2"):
        return "Hydrogen"

    # Gas before electricity: "natural gas for electricity" is still Gas.
    if any(
        k in text
        for k in (
            "natural gas",
            "network gas",
            "biogas",
            "gas for",
            "gas from",
            "supply: natural",
            "fuel tech – biogas",
            "fuel tech - biogas",
        )
    ) or ("GMX" in code_u) or (code_u.endswith("GAS") and "BIOGAS" not in code_u):
        return "Gas"

    # Cooling / service DEM commodities — not grid electricity.
    if "space cool" in text or "cooling" in text:
        return "Commercial cooling"

    if code_u.endswith("SOL") or "solar" in carrier_l or "solar" in text:
        if code_u in {"RENSOL"} or "renewable: solar" in carrier_l or "electricity: solar" in carrier_l:
            return "Electricity"
        return "Heat"
    if "electricity" in text or any(k in carrier_l for k in ("hydro", "nuclear", "wind")):
        return "Electricity"
    if any(k in text for k in ("coal", "coke", "lignite", "hard coal")) or any(
        x in code_u for x in ("COA", "COK", "COL")
    ):
        return "Coal & solids"
    if any(k in text for k in ("heat", "chaleur", "geothermal", "district")):
        return "Heat"
    if any(
        k in text
        for k in (
            "biomass",
            "biofuel",
            "biodiesel",
            "bioethanol",
            "wood",
            "chips",
            "black liquor",
            "rape",
            "starch",
            "waste",
        )
    ):
        return "Biomass & biofuels"
    if any(
        k in text
        for k in (
            "diesel",
            "gasoline",
            "oil",
            "kerosene",
            "lpg",
            "liquified petroleum",
            "naphtha",
            "residual fuel",
            "petroleum",
            "aviation",
            "navigation",
        )
    ) or any(x in code_u for x in ("OIL", "DST", "GSL", "KER", "LPG", "HFO", "LFO", "NAP")):
        return "Oil products"
    # Prefer a concrete TIMES description/code over a generic bucket
    return identity_label(description, carrier, cluster, code)


def infer_carrier_family(
    *,
    description: str = "",
    code: str = "",
    carrier: str = "",
    cluster: str = "",
) -> str:
    """
    Strict energy-carrier family for a commodity.

    Prevents collapsing fundamentally different carriers (e.g. gas into
    Electricity (context)). Returns an overview family name, or "".
    """
    fam = infer_overview_commodity_label(
        description=description, code=code, carrier=carrier, cluster=cluster
    )
    if fam in _OVERVIEW_CARRIER_FAMILIES:
        return fam
    text = f"{carrier} {cluster} {description} {code}".lower()
    for family, keys in (
        ("Electricity", ("electric", "elc", "hydro", "wind", "pv", "battery", "batelc")),
        ("Gas", ("natural gas", "network gas", "biogas", "gmx")),
        ("Heat", ("heat", "chaleur", "geothermal", "district", "hot water", "space heat")),
        ("Oil products", ("diesel", "gasoline", "kerosene", "oil", "lpg", "naphtha", "petroleum")),
        ("Hydrogen", ("hydrogen", "h2")),
        ("Coal & solids", ("coal", "coke", "lignite")),
        ("Biomass & biofuels", ("biomass", "biofuel", "biodiesel", "wood", "ethanol", "black liquor")),
        ("Nuclear fuel", ("uranium", "nuclear fuel", "nucrsv")),
    ):
        if any(k in text for k in keys):
            return family
    return ""


def infer_overview_process_label(
    *,
    sector: str = "",
    process_type: str = "",
    description: str = "",
    agg_level_2: str = "",
    code: str = "",
) -> str:
    """Heuristic role label used when ``sankey_overview`` is missing on a process."""
    sector_u = _clean_text(sector).upper()
    ptype = _clean_text(process_type).upper()
    l2 = _clean_text(agg_level_2).lower()
    desc = _clean_text(description).lower()
    code_u = _clean_text(code)
    code_l = code_u.lower()
    blob = f"{l2} {desc} {code_l}"

    if code_u in {"", "-"}:
        return identity_label(description, "System aggregate")
    # Supply-chain roles — keep parallel flows parallel (never merge MIN into imports,
    # or generation fuel-tech into end-use fuel-tech).
    if code_u.startswith("MIN") or l2 == "local production" or ".min.ire." in blob:
        return "Local production"
    if (
        sector_u == "IMP"
        or code_u.startswith(("IMP", "EXP"))
        or "import" in blob
        or "export" in blob
        or l2 == "imports"
    ):
        # Building retrofit dummies are IRE but not energy imports.
        if "dum_retrofit" in code_l or "retrofit" in l2:
            return "Building retrofits"
        return "Imports & trade"
    if l2 in {"district heating", "commercial heat exchanger"} or (
        "district heat" in blob and "chp" not in blob
    ):
        return "District heating"
    # Heat pumps often have codes like *ELCHP* / *CHPN* — do not treat as CHP.
    is_heat_pump = (
        "heat pump" in blob
        or "heatpump" in blob
        or "ELCHP" in code_u
        or l2.endswith("heat pump")
        or " heat pump" in l2
    )
    if not is_heat_pump and (
        ptype == "CHP"
        or l2 in {"chp", "tertiary chp"}
        or ("chp" in blob and "heat exchanger" not in blob)
    ):
        return "CHP"
    if sector_u == "ELC":
        # Generation fuel-tech feeds power plants (same chain direction).
        return "Power plants"
    if sector_u == "SUP":
        # Biogas methanisation / upgrading = Wallonia local potential (Mt feedstocks
        # are off the energy Sankey; Local production stands in for them).
        if any(
            k in blob
            for k in (
                "methanisation",
                "biogas production",
                "épuration",
                "epuration",
                "digesteur",
                "digestor",
            )
        ) or code_u.startswith(("BWBIOGAZ", "BWSUPGZH", "DIGE")):
            return "Local production"
        return (
            "Fuel conversion"
            if "fuel tech" in blob or ptype in {"PRE", ""}
            else "Fuel supply"
        )
    if "fuel tech" in blob:
        return "End-use fuel tech"
    if l2 == "retrofitting improvements" or code_u.startswith("Retrofit-") or "dum_retrofit" in code_l:
        return "Building retrofits"
    if sector_u == "IND":
        return "Industry"
    if sector_u in {"RSD", "COM"}:
        return "Buildings"
    if sector_u == "TRA":
        return "Transport"
    if sector_u == "AGR":
        return "Agriculture"

    # Code-prefix fallbacks when Sector is missing from dictionaries
    if code_u.startswith(("RW", "RH", "CW", "CH", "CC", "COM", "RSD")):
        return "Buildings"
    if code_u.startswith(("ETSTP", "ECP", "EPV", "EWIND", "ELC")) or "_TGV_" in code_u:
        return "Power plants"
    if code_u.startswith(("IMP", "EXP")):
        return "Imports & trade"
    if code_u.startswith(("TRA", "T")) and any(
        k in code_l for k in ("car", "truck", "bus", "rail", "tra")
    ):
        return "Transport"
    if code_u.startswith("IND") or code_u.startswith("I"):
        if any(k in code_l for k in ("ind", "steel", "cement", "chem")):
            return "Industry"

    return identity_label(description, agg_level_2, code)


def _sector_label(code: str) -> str:
    code = (code or "").strip().upper()
    if not code:
        return ""
    return SECTOR_LABELS.get(code, code)


def _mapping_process_label(row: pd.Series) -> str:
    """Process node label from mapping CSVs / TIMES description / code."""
    for key in ("process_agg", "agg_level_2"):
        val = _clean_text(row.get(key))
        if val:
            return val
    return identity_label(row.get("process"), row.get("process_code"))


def _mapping_commodity_label(row: pd.Series) -> str:
    """Commodity node label from mapping CSVs / TIMES description / code."""
    carrier = _clean_text(row.get("pypsa_carrier"))
    if carrier:
        return carrier
    return identity_label(row.get("commodity"), row.get("commodity_code"))


def _col_or_empty(df: pd.DataFrame, name: str) -> list:
    """Column values as a Python list, or empty strings aligned to ``df``."""
    if name in df.columns:
        return df[name].tolist()
    return [""] * len(df)


def _mapping_process_series(df: pd.DataFrame) -> pd.Series:
    """Vectorized equivalent of ``_mapping_process_label`` over a frame."""
    out = pd.Series("", index=df.index, dtype=object)
    for key in ("process_agg", "agg_level_2"):
        if key in df.columns:
            cand = _nonempty_labels(df[key])
            out = out.where(out.ne(""), cand)
    still = out.eq("")
    if still.any():
        out = out.where(~still, _process_identity_series(df))
    return out


def _mapping_commodity_series(df: pd.DataFrame) -> pd.Series:
    """Vectorized equivalent of ``_mapping_commodity_label`` over a frame."""
    if "pypsa_carrier" in df.columns:
        out = _nonempty_labels(df["pypsa_carrier"])
    else:
        out = pd.Series("", index=df.index, dtype=object)
    still = out.eq("")
    if still.any():
        out = out.where(~still, _commodity_identity_series(df))
    return out


def _carrier_label(carrier: str, commodity: str, commodity_code: str) -> str:
    """Legacy helper: CSV carrier, else TIMES description/code."""
    return identity_label(carrier, commodity, commodity_code)


def _process_identity_series(df: pd.DataFrame) -> pd.Series:
    desc = _col_or_empty(df, "process")
    code = _col_or_empty(df, "process_code")
    return pd.Series(
        [identity_label(d, c) for d, c in zip(desc, code)],
        index=df.index,
        dtype=object,
    )


def _commodity_identity_series(df: pd.DataFrame) -> pd.Series:
    desc = _col_or_empty(df, "commodity")
    code = _col_or_empty(df, "commodity_code")
    return pd.Series(
        [identity_label(d, c) for d, c in zip(desc, code)],
        index=df.index,
        dtype=object,
    )


def _overview_process_series(df: pd.DataFrame) -> pd.Series:
    """Overview process labels without ``DataFrame.iterrows`` (avoids deepcopies)."""
    sector = _col_or_empty(df, "sector")
    process_type = _col_or_empty(df, "process_type")
    description = _col_or_empty(df, "process")
    if "agg_level_2" in df.columns:
        agg_level_2 = df["agg_level_2"].tolist()
    elif "process_agg" in df.columns:
        agg_level_2 = df["process_agg"].tolist()
    else:
        agg_level_2 = [""] * len(df)
    code = _col_or_empty(df, "process_code")
    return pd.Series(
        [
            infer_overview_process_label(
                sector=s,
                process_type=pt,
                description=d,
                agg_level_2=a,
                code=c,
            )
            for s, pt, d, a, c in zip(sector, process_type, description, agg_level_2, code)
        ],
        index=df.index,
        dtype=object,
    )


def _overview_commodity_series(df: pd.DataFrame) -> pd.Series:
    """Overview commodity labels without ``DataFrame.iterrows``."""
    description = _col_or_empty(df, "commodity")
    code = _col_or_empty(df, "commodity_code")
    carrier = _col_or_empty(df, "pypsa_carrier")
    cluster = _col_or_empty(df, "com_agg__Aggregation Level 1")
    return pd.Series(
        [
            infer_overview_commodity_label(
                description=d,
                code=c,
                carrier=car,
                cluster=clu,
            )
            for d, c, car, clu in zip(description, code, carrier, cluster)
        ],
        index=df.index,
        dtype=object,
    )


# Soft-link QA working view: keep export-touching Aggregation Level 2 labels,
# collapse everything else so the whole-system Sankey stays readable.
CUSTOM_PROCESS_FRIENDLY: dict[str, str] = {
    "residential other": "Household electrical appliances",
    "commercial other": "Commercial electrical appliances",
    "Retrofitting improvements": "Building retrofits",
    # Import-path H₂ delivery (INDGH2C01_i), not the IMPH2 trade process itself
    "hydrogen imports": "imported H2 delivery",
    # EV charge path: chargers → TRA_STG → vehicles
    "TRA_STG_PJ_GW": "EV battery storage",
    "EV charger": "EV chargers",
}

# Context labels kept as-is (not collapsed to ``Transport (other)`` / sector other).
CUSTOM_KEEP_CONTEXT_LABELS = frozenset(
    {
        "EV chargers",
        "EV battery storage",
    }
)

# Allowed ``custom`` subclasses when Aggregation Level 2 is export-touching
# ``commercial other`` (soft-link stays on L2; Sankey splits end-use types).
CUSTOM_COMMERCIAL_OTHER_SPLITS = frozenset(
    {
        "Commercial electrical appliances",
        "Commercial cooling",
        "Commercial lighting",
        "Commercial cooking",
    }
)

# Building stock sinks (context): keep residential vs commercial distinct.
CUSTOM_BUILDINGS_SPLIT_LABELS = frozenset(
    {
        "Residential buildings",
        "Commercial buildings",
        "Residential cooking",
    }
)

_CONTEXT_CARRIER_FAMILIES = frozenset(
    {
        "Electricity",
        "Heat",
        "Gas",
        "Oil products",
        "Coal & solids",
        "Biomass & biofuels",
        "Hydrogen",
        "Nuclear fuel",
    }
)


def friendly_custom_process_label(l2: str) -> str:
    """Rename a few Aggregation Level 2 buckets for the working Sankey."""
    text = _clean_text(l2)
    return CUSTOM_PROCESS_FRIENDLY.get(text, text)


# Primary / generation supply-chain roles (parallel upstream steps).
# Do NOT include "End-use fuel tech" here — export-touching fuel-tech L2 must win.
CUSTOM_SUPPLY_CHAIN_LABELS = frozenset(
    {
        "Imports & trade",
        "Local production",  # includes Wallonia biogas methanisation / upgrading
        "Fuel supply",
        "Fuel conversion",
        "Power plants",
        "CHP",
        "District heating",
        # Wallonia renewable generation kept separate in the working Sankey
        "PV",
        "Onshore wind",
        "Building retrofits",
        # Electricity storage (charge in / discharge out) — keep out of Power plants
        "Grid battery storage",
        "Pumped hydro",
        "Household battery storage",
        "EV battery storage",
    }
)

# Prefer these over coarser Power plants when set in CSV custom.
# Offshore wind is outside Wallonia — leave IMPELCOFFWIN* under Imports & trade.
CUSTOM_GENERATION_SPLIT_LABELS = frozenset(
    {"PV", "Onshore wind", "Grid battery storage", "Pumped hydro"}
)

# Soft-link commodity buckets from extraction_rules.csv (fuel / carrier side).
# Used to disaggregate leftover "End-use fuel tech" by rule commodity.
_RULE_FUEL_CATEGORIES = frozenset(
    {
        "electricity",
        "coal",
        "coke",
        "hydrogen",
        "methane",
        "methanol",
        "naphtha",
        "solid biomass",
        "ammonia",
        "low-temperature heat",
        "total electricity residential",
        "total electricity services",
        "electricity road",
        "electricity rail",
        "hydrogen road",
        "total agriculture electricity",
        "total agriculture heat",
        "total agriculture machinery",
    }
)

# process_agg (Aggregation Level 2) → extraction_rules commodity category
_PROCESS_AGG_TO_RULE_COMMODITY: dict[str, str] = {
    # electricity
    "Fuel Tech - Electricity": "electricity",
    "Fuel Tech - Electricity (IND)": "electricity",
    "Fuel Tech - Electricity (TRA)": "electricity road",
    "Fuel Tech - Solar (IND)": "electricity",
    "PV industrial": "electricity",
    "residential other": "total electricity residential",
    "commercial other": "total electricity services",
    "rail transport": "electricity rail",
    "TRA_STG_PJ_GW": "electricity road",
    # solid fuels / industry
    "Fuel Tech - Hard Coal (IND)": "coal",
    "Fuel Tech - Lignite (IND)": "coal",
    "Fuel Tech - Coke (IND)": "coke",
    "Fuel Tech - Wood material (IND)": "solid biomass",
    "Fuel Tech - Waste Renewable (IND)": "solid biomass",
    "Fuel Tech - Wood CHIPS (IND)": "solid biomass",
    "Fuel Tech - Biofuel (IND)": "solid biomass",
    "Fuel Tech - Wood Chips": "solid biomass",
    "Fuel Tech - Pellets": "solid biomass",
    "Fuel Tech - Wood Pellets": "solid biomass",
    # methane / gas
    "Fuel Tech - Biogas (IND)": "methane",
    "Fuel Tech - Natural Gas transport (IND)": "methane",
    "Fuel Tech New - Gas and Cog industry (IND)": "methane",
    "Fuel Tech - Natural Gas and biogas mixed  (IND)": "methane",
    "Fuel Tech - Liquified Petroleum Gas (IND)": "methane",
    "Fuel Tech New - biogaz epure (IND)": "methane",
    "Fuel Tech - Biogas": "methane",
    "Fuel Tech - Natural Gas biogas mixed": "methane",
    "Fuel Tech - gas mix réseau": "methane",
    "Fuel Tech  - Natural Gas": "methane",
    "Fuel Tech - Natural Gas (TRA)": "methane",
    "Fuel Tech - Reseau gas mixed (TRA)": "methane",
    "Fuel Tech - Biogas (TRA)": "methane",
    "Fuel Tech - biogaz enrichi": "methane",
    "BioGas (TAR)": "methane",
    # oils / naphtha (industry soft-link) vs transport/other oil fuel-tech display labels
    "Fuel Tech - Heavy Fuel Oil (IND)": "naphtha",
    "Fuel Tech - Light Fuel Oil (IND)": "naphtha",
    "Non-energy": "naphtha",
    "Fuel Tech - Oil": "oil",
    "Oil": "oil",
    "Fuel Tech - Diesel (TRA)": "diesel",
    "Fuel Tech - Gasoline (TRA)": "gasoline",
    "Fuel Tech - GSL": "gasoline",
    "Fuel Tech - Kerosene - Jet Fuels": "kerosene",
    "Fuel Tech - Liquified Petroleum Gas": "lpg",
    "Fuel Tech - Liquified Petroleum Gas (TRA)": "lpg",
    "Gasoline": "gasoline",
    # biofuels transport
    "Fuel Tech - Biodiesel (TRA)": "solid biomass",
    "Fuel Tech – Biodiesel": "solid biomass",
    "Fuel Tech - Ethanol (TRA)": "solid biomass",
    "Biofuels": "solid biomass",
    # hydrogen
    "Fuel Tech - H2": "hydrogen",
    "hydrogen for industry": "hydrogen",
    "hydrogen imports": "hydrogen",
    # heat
    "Geothermal (IND)": "low-temperature heat",
    "Fuel Tech - Geothermal": "low-temperature heat",
    "Fuel Tech - Low Temperature Heat": "low-temperature heat",
    "Fuel Tech - Low Temparature Heat": "low-temperature heat",
    "Fuel Tech - Heat": "low-temperature heat",
    "Fuel Tech 1 - Heat": "low-temperature heat",
    "Fuel Tech 2 - Heat": "low-temperature heat",
    "Fuel Tech 0 - Heat": "low-temperature heat",
    "industry high temperature heat": "low-temperature heat",
}


def rule_commodity_for_fuel_tech(
    *,
    process_agg: str = "",
    description: str = "",
    code: str = "",
    carrier: str = "",
) -> str:
    """
    Map an end-use fuel-tech process to an extraction_rules commodity category.

    Prefer Aggregation Level 2 labels used in ``extraction_rules.csv``; fall back
    to carrier-family heuristics aligned with those rule commodities.
    """
    l2 = _clean_text(process_agg)
    if l2 in _PROCESS_AGG_TO_RULE_COMMODITY:
        return _PROCESS_AGG_TO_RULE_COMMODITY[l2]
    # Partial match on known Fuel Tech prefixes
    for key, cat in _PROCESS_AGG_TO_RULE_COMMODITY.items():
        if key.lower() in l2.lower() or key.lower() in _clean_text(description).lower():
            return cat
    fam = infer_carrier_family(
        description=description, code=code, carrier=carrier, cluster=process_agg
    )
    fam_to_rule = {
        "Electricity": "electricity",
        "Gas": "methane",
        "Oil products": "naphtha",
        "Coal & solids": "coal",
        "Biomass & biofuels": "solid biomass",
        "Hydrogen": "hydrogen",
        "Heat": "low-temperature heat",
    }
    return fam_to_rule.get(fam, "other")


def end_use_fuel_tech_label(
    *,
    process_agg: str = "",
    description: str = "",
    code: str = "",
    carrier: str = "",
) -> str:
    """Disaggregated end-use fuel-tech label: ``Fuel tech · {rule commodity}``."""
    cat = rule_commodity_for_fuel_tech(
        process_agg=process_agg,
        description=description,
        code=code,
        carrier=carrier,
    )
    return f"Fuel tech · {cat}"


def _generation_split_label(row: pd.Series) -> str:
    """Detect PV / onshore wind generation from code or description."""
    code = _clean_text(row.get("process_code") or row.get("Technology (Process)"))
    desc = _clean_text(row.get("process") or row.get("Description")).lower()
    l2 = _clean_text(row.get("agg_level_2") or row.get("process_agg")).lower()
    # Offshore wind is outside Wallonia — do not split out of Imports & trade.
    if "offwin" in code.lower() or "off wind" in desc or "offshore" in f"{code} {desc} {l2}".lower():
        return ""
    if (
        code.startswith("ERNW_WINON")
        or "Eolien" in code
        or code == "ELCWIN00"
        or l2 in {"wind turbine", "fuel tech - wind"}
        or "wind onshore" in desc
        or (l2 == "renewables" and "wind" in desc and "off" not in desc)
    ):
        return "Onshore wind"
    if (
        "PV-" in code
        or code.endswith("PVELC")
        or code in {"ELCSOL00", "RSDSOL00", "COMSOL00"}
        or l2 in {"pv", "pv residential", "pv commercial", "pv industrial"}
        or (l2 == "solar" and _clean_text(row.get("sector")).upper() in {"ELC", "RSD", "COM"})
        or re.search(r"\bpv\b", desc)
    ):
        if "water heat" in desc or "solar thermal" in l2:
            return ""
        return "PV"
    return ""


def _buildings_split_label(row: pd.Series) -> str:
    """Residential vs commercial building sinks from CSV custom / L2 / sector."""
    csv = _clean_text(row.get("proc_agg__custom"))
    if csv in CUSTOM_BUILDINGS_SPLIT_LABELS:
        return csv
    l2 = _clean_text(row.get("agg_level_2") or row.get("process_agg"))
    sector = _clean_text(row.get("sector")).upper()
    code = _clean_text(row.get("process_code"))
    if l2 == "Buildings: built area" or code.startswith("RDW_"):
        return "Residential buildings"
    if (
        l2.startswith("Building Existing")
        or l2.startswith("Building New")
        or code.startswith("COM_CBAT_")
        or code.startswith("COM_CNBAT_")
    ):
        return "Commercial buildings"
    if l2 == "residential cooking":
        return "Residential cooking"
    if l2 == "degree-days correction for the base year":
        return "Residential buildings"
    if sector == "RSD" and l2.lower().startswith("building"):
        return "Residential buildings"
    if sector == "COM" and "building" in l2.lower():
        return "Commercial buildings"
    return ""


def _context_process_label_row(row: pd.Series) -> str:
    """Coarse role label for processes that are not export-touching at L2."""
    bldg = _buildings_split_label(row)
    if bldg:
        return bldg
    # Honour PV / wind splits and other supply-chain custom labels first.
    for key in ("proc_agg__custom", "proc_agg__sankey_overview"):
        lab = _clean_text(row.get(key))
        if lab in CUSTOM_BUILDINGS_SPLIT_LABELS:
            return lab
        if lab in CUSTOM_GENERATION_SPLIT_LABELS or lab in CUSTOM_SUPPLY_CHAIN_LABELS:
            if lab == "Power plants":
                split = _generation_split_label(row)
                if split:
                    return split
            return lab
        if lab == "End-use fuel tech" or lab.startswith("Fuel tech ·"):
            if lab.startswith("Fuel tech ·"):
                return lab
            return end_use_fuel_tech_label(
                process_agg=row.get("agg_level_2", row.get("process_agg", "")),
                description=row.get("process", ""),
                code=row.get("process_code", ""),
                carrier=row.get("pypsa_carrier", ""),
            )
        if lab == "CHP & district heat":
            # Legacy merged label — split using type / L2.
            return infer_overview_process_label(
                sector=row.get("sector", ""),
                process_type=row.get("process_type", ""),
                description=row.get("process", ""),
                agg_level_2=row.get("agg_level_2", row.get("process_agg", "")),
                code=row.get("process_code", ""),
            )
    split = _generation_split_label(row)
    if split:
        return split
    overview = _clean_text(row.get("proc_agg__sankey_overview"))
    if overview:
        # Distinguish leftover sector mass from named export end-uses.
        if overview in {"Buildings", "Industry", "Transport", "Agriculture"}:
            if overview == "Buildings":
                bldg = _buildings_split_label(row)
                if bldg:
                    return bldg
            return f"{overview} (other)"
        if overview == "Fuel conversion" or overview == "End-use fuel tech":
            return end_use_fuel_tech_label(
                process_agg=row.get("agg_level_2", row.get("process_agg", "")),
                description=row.get("process", ""),
                code=row.get("process_code", ""),
                carrier=row.get("pypsa_carrier", ""),
            )
        if overview == "CHP & district heat":
            return infer_overview_process_label(
                sector=row.get("sector", ""),
                process_type=row.get("process_type", ""),
                description=row.get("process", ""),
                agg_level_2=row.get("agg_level_2", row.get("process_agg", "")),
                code=row.get("process_code", ""),
            )
        return overview
    label = infer_overview_process_label(
        sector=row.get("sector", ""),
        process_type=row.get("process_type", ""),
        description=row.get("process", ""),
        agg_level_2=row.get("agg_level_2", row.get("process_agg", "")),
        code=row.get("process_code", ""),
    )
    if label in {"Buildings", "Industry", "Transport", "Agriculture"}:
        if label == "Buildings":
            bldg = _buildings_split_label(row)
            if bldg:
                return bldg
        return f"{label} (other)"
    if label == "Fuel conversion" or label == "End-use fuel tech":
        return end_use_fuel_tech_label(
            process_agg=row.get("agg_level_2", row.get("process_agg", "")),
            description=row.get("process", ""),
            code=row.get("process_code", ""),
            carrier=row.get("pypsa_carrier", ""),
        )
    if label == "Power plants":
        split = _generation_split_label(row)
        if split:
            return split
    return label


def _context_commodity_label(family: str) -> str:
    """Keep context carriers from colliding with exported sector electricity/heat."""
    fam = _clean_text(family)
    if fam == "Nuclear fuel":
        return "Nuclear fuel"
    if fam in _CONTEXT_CARRIER_FAMILIES:
        return f"{fam} (context)"
    return fam or "Other"


def refine_custom_labels_for_readability(
    proc_labels: pd.Series,
    com_labels: pd.Series,
    df: pd.DataFrame,
) -> tuple[pd.Series, pd.Series]:
    """
    Tighten ``custom`` Sankey labels when export tags are available.

    Rules (readable working view):
    - Process: keep supply-chain role labels (Imports, Local production, Power
      plants, …). Keep Aggregation Level 2 that appears on any exported row
      (soft-link grain, with friendly renames). Collapse remaining context to
      overview / ``(other)`` buckets — never merge upstream with downstream.
    - Commodity: codes that appear on any exported row keep one specific label
      for all rows of that code (hub stays intact for collapse); other carriers
      become ``{family} (context)``. Bare family names like ``Electricity`` are
      replaced by TIMES descriptions when used as export hubs.
    """
    if "exported" not in df.columns or df.empty:
        return proc_labels, com_labels

    exported = df["exported"].fillna(False).astype(bool)
    if not bool(exported.any()):
        return proc_labels, com_labels

    l2 = pd.Series("", index=df.index, dtype=object)
    for key in ("process_agg", "agg_level_2", "proc_agg__Aggregation Level 2"):
        if key in df.columns:
            cand = _nonempty_labels(df[key])
            l2 = l2.where(l2.ne(""), cand)
    export_l2 = set(l2.loc[exported].tolist()) - {""}

    export_com_codes: set[str] = set()
    if "commodity_code" in df.columns:
        export_com_codes = {
            str(c)
            for c in df.loc[exported, "commodity_code"].tolist()
            if _clean_text(c)
        }

    # Prefer a specific display name per exported commodity code (avoid bare
    # "Electricity"/"Heat" that collide with context carriers).
    export_com_label: dict[str, str] = {}
    exp_idx = df.index[exported]
    com_lab_list = com_labels.tolist()
    index_pos = {idx: i for i, idx in enumerate(df.index)}
    com_codes = _col_or_empty(df, "commodity_code")
    com_descs = _col_or_empty(df, "commodity")
    carriers = _col_or_empty(df, "pypsa_carrier")
    for idx in exp_idx:
        pos = index_pos[idx]
        code = _clean_text(com_codes[pos])
        if not code or code in export_com_label:
            continue
        csv_lab = _clean_text(com_lab_list[pos])
        desc = _clean_text(com_descs[pos])
        carrier = _clean_text(carriers[pos])
        # Bare family names / pre-baked "{family} (context)" are too coarse for
        # export hubs — prefer description (avoids OILDST stuck as Oil products
        # (context) and merging with kerosene).
        if (
            csv_lab
            and csv_lab not in _CONTEXT_CARRIER_FAMILIES
            and not csv_lab.endswith("(context)")
            and csv_lab not in {
                "Natural Gas",
                "Network gas",
            }
        ):
            export_com_label[code] = csv_lab
        else:
            export_com_label[code] = identity_label(desc, carrier, csv_lab, code)

    # Materialize rows once — much faster than DataFrame.iterrows().
    rows = df.to_dict("records")
    proc_lab_list = proc_labels.tolist()
    l2_list = l2.tolist()
    _coarse_custom = {
        "",
        "End-use fuel tech",
        "CHP & district heat",
        "Fuel conversion",
    }
    friendly_values = frozenset(CUSTOM_PROCESS_FRIENDLY.values())

    new_proc = []
    for pos, row in enumerate(rows):
        csv_lab = _clean_text(proc_lab_list[pos])
        # PV / onshore wind before coarser Power plants buckets.
        split = _generation_split_label(row)
        if csv_lab in CUSTOM_GENERATION_SPLIT_LABELS:
            new_proc.append(csv_lab)
            continue
        if split:
            new_proc.append(split)
            continue
        # Always keep explicit supply-chain roles (parallel primary/conversion).
        if csv_lab in CUSTOM_SUPPLY_CHAIN_LABELS:
            new_proc.append(csv_lab)
            continue
        overview = _clean_text(row.get("proc_agg__sankey_overview"))
        # Prefer a specific custom / L2 label over a coarse overview supply-chain
        # bucket (e.g. heat pumps wrongly tagged sankey_overview=CHP).
        if overview in CUSTOM_SUPPLY_CHAIN_LABELS and csv_lab in _coarse_custom:
            new_proc.append(overview)
            continue
        # Legacy merged CHP/DH bucket
        if csv_lab == "CHP & district heat" or (
            overview == "CHP & district heat" and csv_lab in _coarse_custom
        ):
            new_proc.append(
                infer_overview_process_label(
                    sector=row.get("sector", ""),
                    process_type=row.get("process_type", ""),
                    description=row.get("process", ""),
                    agg_level_2=row.get("agg_level_2", row.get("process_agg", "")),
                    code=row.get("process_code", ""),
                )
            )
            continue

        row_l2 = _clean_text(l2_list[pos])
        # End-use fuel techs: keep export-touching L2; else split by rule commodity.
        if csv_lab == "End-use fuel tech" or csv_lab.startswith("Fuel tech ·") or overview == "End-use fuel tech":
            if row_l2 and row_l2 in export_l2:
                new_proc.append(friendly_custom_process_label(row_l2))
            elif csv_lab.startswith("Fuel tech ·"):
                new_proc.append(csv_lab)
            else:
                new_proc.append(
                    end_use_fuel_tech_label(
                        process_agg=row_l2 or row.get("process_agg", ""),
                        description=row.get("process", ""),
                        code=row.get("process_code", ""),
                        carrier=row.get("pypsa_carrier", ""),
                    )
                )
            continue

        if row_l2 and row_l2 in export_l2:
            friendly = friendly_custom_process_label(row_l2)
            # Keep commercial-other subclasses (cooling / lighting / …) so the
            # Sankey does not dump all services electricity into one node.
            if row_l2 == "commercial other" and csv_lab in CUSTOM_COMMERCIAL_OTHER_SPLITS:
                new_proc.append(csv_lab)
            elif csv_lab in friendly_values:
                new_proc.append(csv_lab)
            elif csv_lab and csv_lab == friendly:
                new_proc.append(csv_lab)
            else:
                new_proc.append(friendly)
        elif csv_lab in CUSTOM_KEEP_CONTEXT_LABELS:
            # EV chargers etc.: context but intentionally named (not Transport (other)).
            new_proc.append(csv_lab)
        else:
            new_proc.append(_context_process_label_row(row))
    proc_out = pd.Series(new_proc, index=df.index, dtype=object)

    overview_com = _overview_commodity_series(df)
    overview_com_list = overview_com.tolist()
    new_com = []
    for pos, row in enumerate(rows):
        code = _clean_text(row.get("commodity_code"))
        if code and code in export_com_codes:
            # Same label for every row of an export-touching commodity code so
            # producers and consumers stay in one hub (needed for collapse).
            new_com.append(export_com_label.get(code) or _clean_text(com_lab_list[pos]))
        else:
            csv = _clean_text(com_lab_list[pos])
            # Strict family from TIMES text — never put gas under Electricity (context).
            fam = infer_carrier_family(
                description=row.get("commodity", ""),
                code=code,
                carrier=row.get("pypsa_carrier", ""),
                cluster=row.get("com_agg__Aggregation Level 1", ""),
            ) or _clean_text(overview_com_list[pos])
            # Keep specific CSV labels (e.g. Imported electricity / ELCIMP) so they
            # do not dissolve into Electricity (context) and create cross-links.
            # But never keep a label that contradicts the carrier family
            # (e.g. a gas commodity wrongly tagged Electricity (context)).
            # Also never keep a pre-baked "{family} (context)" CSV label when a
            # more specific Description/Cluster exists — those merges create
            # spurious process→process ribbons (diesel↔kerosene, INDELC↔BATELCOUT).
            csv_fam = ""
            if csv.endswith("(context)"):
                csv_fam = csv[: -len(" (context)")].strip()
            elif csv in _CONTEXT_CARRIER_FAMILIES:
                csv_fam = csv
            desc = _clean_text(row.get("commodity", ""))
            specific = identity_label(
                desc,
                _clean_text(row.get("pypsa_carrier", "")),
                "" if csv_fam else csv,
                code,
            )
            if fam and csv_fam and fam != csv_fam:
                new_com.append(_context_commodity_label(fam))
            elif csv_fam:
                # Prefer description over coarse context bucket.
                new_com.append(specific if specific and specific != csv else _context_commodity_label(fam or csv_fam))
            elif (
                csv
                and csv not in _CONTEXT_CARRIER_FAMILIES
                and not csv.endswith("(context)")
                and csv.lower() not in {"natural gas", "network gas"}
            ):
                new_com.append(csv)
            else:
                new_com.append(_context_commodity_label(fam or csv))
    com_out = pd.Series(new_com, index=df.index, dtype=object)
    return proc_out, com_out


def _apply_legacy_mapping_labels(df: pd.DataFrame) -> None:
    df["process_node"] = _mapping_process_series(df)
    df["commodity_node"] = _mapping_commodity_series(df)


def _apply_column_agg_labels(df: pd.DataFrame, resolved: str) -> None:
    pcol = proc_agg_col(resolved)
    ccol = com_agg_col(resolved)
    proc_labels = _nonempty_labels(df[pcol])
    com_labels = _nonempty_labels(df[ccol])

    if resolved == "sankey_overview":
        proc_labels = proc_labels.where(proc_labels.ne(""), _overview_process_series(df))
        com_labels = com_labels.where(com_labels.ne(""), _overview_commodity_series(df))
    else:
        if resolved in {"Aggregation Level 1", "Aggregation Level 2", "custom"}:
            proc_fallback = _mapping_process_series(df)
        else:
            proc_fallback = _process_identity_series(df)
        if resolved in {"Aggregation Level 2", "custom"}:
            com_fallback = _mapping_commodity_series(df)
        else:
            com_fallback = _commodity_identity_series(df)
        proc_labels = proc_labels.where(proc_labels.ne(""), proc_fallback)
        com_labels = com_labels.where(com_labels.ne(""), com_fallback)

        # custom: still-empty process labels → description-based cluster heuristics
        if resolved == "custom":
            still = proc_labels.eq("") | proc_labels.str.lower().eq("unknown")
            if still.any():
                proc_labels = proc_labels.where(~still, _overview_process_series(df))

    # Final safety: never emit empty / Unknown
    still_p = proc_labels.eq("") | proc_labels.str.lower().eq("unknown")
    still_c = com_labels.eq("") | com_labels.str.lower().eq("unknown")
    if still_p.any():
        proc_labels = proc_labels.where(~still_p, _process_identity_series(df))
    if still_c.any():
        com_labels = com_labels.where(~still_c, _commodity_identity_series(df))

    # Working `custom` view: keep PyPSA export L2 grain, collapse context (B2).
    if resolved == "custom":
        proc_labels, com_labels = refine_custom_labels_for_readability(
            proc_labels, com_labels, df
        )

    df["process_node"] = proc_labels
    df["commodity_node"] = com_labels


def aggregate_flows(
    flows: pd.DataFrame,
    level: str = "L0",
    *,
    apply_netting: bool = True,
    drop_empty_labels: bool = True,
) -> pd.DataFrame:
    """
    Collapse process / commodity labels to an aggregation level.

    ``level`` is a CSV column name shared by process and commodity mappings,
    or a legacy alias:

    - ``mapping`` / ``Aggregation Level 2``: process_agg × pypsa_carrier (QA default)
    - ``L0`` / ``Sector``: coarse sector view
    - ``L1`` / ``Aggregation Level 1``: mid-level process grouping
    - ``L2``: individual process_code + commodity_code (no collapse)

    When enriched ``proc_agg__*`` / ``com_agg__*`` columns exist on ``flows``,
    labels are taken from those columns (vectorized). Otherwise ``mapping`` /
    ``Aggregation Level 2`` fall back to legacy ``process_agg`` / ``pypsa_carrier``
    columns.

    Returns a frame with columns suitable for Sankey:
        year, region, variable, process_code, process, commodity_code, commodity, value
    where process_code/commodity_code hold the aggregated node ids.
    """
    if flows.empty:
        return flows.copy()

    df = flows.copy()
    df = df[df["variable"].str.upper().isin(["VAR_FIN", "VAR_FOUT"])].copy()
    if df.empty:
        return df

    resolved = _resolve_agg_level(level)

    if resolved == "L2" or level == "L2":
        df["process_node"] = df["process_code"].astype(str)
        df["commodity_node"] = df["commodity_code"].astype(str)
    elif level == "L0":
        df["process_node"] = _nonempty_labels(df["sector"].map(_sector_label))
        df["process_node"] = df["process_node"].where(
            df["process_node"].ne(""), _process_identity_series(df)
        )
        carriers = _col_or_empty(df, "pypsa_carrier")
        commodities = _col_or_empty(df, "commodity")
        commodity_codes = _col_or_empty(df, "commodity_code")
        if "commodity_sector" in df.columns:
            com_sectors = df["commodity_sector"].tolist()
            com_nodes = [
                (
                    _sector_label(cs)
                    if str(cs or "").strip()
                    else _carrier_label(car, com, code)
                )
                for cs, car, com, code in zip(
                    com_sectors, carriers, commodities, commodity_codes
                )
            ]
        else:
            com_nodes = [
                _carrier_label(car, com, code)
                for car, com, code in zip(carriers, commodities, commodity_codes)
            ]
        df["commodity_node"] = _nonempty_labels(pd.Series(com_nodes, index=df.index))
        df["commodity_node"] = df["commodity_node"].where(
            df["commodity_node"].ne(""), _commodity_identity_series(df)
        )
    else:
        pcol = proc_agg_col(resolved)
        ccol = com_agg_col(resolved)
        has_proc = pcol in df.columns
        has_com = ccol in df.columns
        mapping_fallback = resolved == "Aggregation Level 2" or level == "mapping"

        if has_proc and has_com:
            _apply_column_agg_labels(df, resolved)
        elif mapping_fallback:
            _apply_legacy_mapping_labels(df)
        else:
            available = _available_agg_levels(df)
            raise ValueError(
                f"Aggregation level {level!r} (resolved {resolved!r}) is not available "
                f"on enriched flows (missing {pcol!r} or {ccol!r}). "
                f"Available levels: {available or ['L0', 'L1', 'L2', 'mapping']}"
            )

    if drop_empty_labels:
        df = df[
            (df["process_node"].astype(str).str.strip() != "")
            & (df["commodity_node"].astype(str).str.strip() != "")
        ]

    # Select Sankey columns without a second full deep-copy of enriched frames.
    out_cols = {
        "year": df["year"],
        "region": df["region"],
        "variable": df["variable"],
        "commodity_code": df["commodity_node"],
        "commodity": df["commodity_node"],
        "process_code": df["process_node"],
        "process": df["process_node"],
        "value": df["value"],
    }
    for extra in ("matched_categories", "exported"):
        if extra in df.columns:
            out_cols[extra] = df[extra]
    out = pd.DataFrame(out_cols)

    if apply_netting:
        tags = None
        if "matched_categories" in df.columns or "exported" in df.columns:
            tag_cols = ["year", "region", "process_node", "commodity_node", "variable"]
            tag_cols.extend(
                c for c in ("matched_categories", "exported") if c in df.columns
            )
            tags = df.loc[:, tag_cols]

        netted = net_bidirectional_links(out)
        if tags is not None and not netted.empty:
            tag_agg = tags
            if "exported" in tag_agg.columns:
                exp_bool = tag_agg["exported"].fillna(False).astype(bool)
                keys = ["year", "region", "process_node", "commodity_node"]
                grouped = exp_bool.groupby([tag_agg[k] for k in keys], sort=False)
                any_exp = grouped.any()
                all_exp = grouped.all()
                status = pd.Series("context", index=any_exp.index, dtype=object)
                status = status.mask(any_exp & all_exp, "exported")
                status = status.mask(any_exp & ~all_exp, "mixed")
                exp = status.reset_index(name="export_status")
                exp.columns = keys + ["export_status"]
                netted = netted.merge(
                    exp,
                    left_on=["year", "region", "process_code", "commodity_code"],
                    right_on=["year", "region", "process_node", "commodity_node"],
                    how="left",
                )
                netted["export_status"] = netted["export_status"].fillna("context")
                netted["exported"] = netted["export_status"].eq("exported")
                netted = netted.drop(
                    columns=[
                        c for c in ("process_node", "commodity_node") if c in netted.columns
                    ]
                )
            if "matched_categories" in tag_agg.columns:

                def _join_cats(series):
                    cats: set[str] = set()
                    for item in series:
                        if isinstance(item, list):
                            cats.update(item)
                        elif isinstance(item, str) and item:
                            cats.update(x for x in item.split("|") if x)
                    return "|".join(sorted(cats))

                cats = (
                    tag_agg.groupby(
                        ["year", "region", "process_node", "commodity_node"],
                        sort=False,
                    )["matched_categories"]
                    .agg(_join_cats)
                    .reset_index()
                )
                netted = netted.merge(
                    cats,
                    left_on=["year", "region", "process_code", "commodity_code"],
                    right_on=["year", "region", "process_node", "commodity_node"],
                    how="left",
                )
                netted = netted.drop(
                    columns=[
                        c for c in ("process_node", "commodity_node") if c in netted.columns
                    ]
                )
        out = netted

    logger.info(
        "Aggregated to %s: %d rows, %d process nodes, %d commodity nodes",
        level,
        len(out),
        out["process_code"].nunique() if not out.empty else 0,
        out["commodity_code"].nunique() if not out.empty else 0,
    )
    return out


def classify_export_status(exported: pd.Series) -> str:
    """
    Classify aggregated link export state.

    - exported: all contributing flows are exported to pypsa-wal
    - context: none are exported
    - mixed: exported and non-exported flows were merged (e.g. mapping aggregation)
    """
    exp = exported.fillna(False).astype(bool)
    has_exp = bool(exp.any())
    has_non = bool((~exp).any())
    if has_exp and has_non:
        return "mixed"
    if has_exp:
        return "exported"
    return "context"


def merge_export_statuses(
    statuses: pd.Series,
    *,
    any_exported: bool = False,
) -> str:
    """
    Combine row-level export statuses when collapsing or aggregating Sankey links.

    Statuses: ``exported``, ``context``, ``mixed``, ``double_count``.

    Default (``any_exported=False``): ``exported`` + ``context`` → ``mixed``
    (strict; used when several physical rows share one bipartite link).

    With ``any_exported=True`` (commodity-hub collapse to process→process):
    ``exported`` + ``context`` → ``exported``. A balanced collapse is one Sankey
    flow; a single exported endpoint is enough to colour it blue for PyPSA.

    ``double_count`` = both FOut and FIn endpoints exported (soft-link overlap risk).
    ``mixed`` on either side still wins over blue/grey.
    """
    values = {
        str(v)
        for v in statuses.dropna()
        if str(v) in {"exported", "context", "mixed", "double_count"}
    }
    if "mixed" in values:
        return "mixed"
    if "double_count" in values:
        return "double_count"
    if "exported" in values and "context" in values:
        return "exported" if any_exported else "mixed"
    if "exported" in values:
        return "exported"
    return "context"


def collapse_pair_export_status(prod_status: str, cons_status: str) -> str:
    """
    Colour a collapsed producer→consumer link from the two endpoint statuses.

    - both ``exported`` → ``double_count`` (purple; FOut and FIn both soft-linked)
    - exactly one ``exported`` → ``exported`` (blue)
    - either ``mixed`` → ``mixed``
    - else ``context``
    """
    p = str(prod_status or "context")
    c = str(cons_status or "context")
    if p == "mixed" or c == "mixed":
        return "mixed"
    if p == "double_count" or c == "double_count":
        return "double_count"
    if p == "exported" and c == "exported":
        return "double_count"
    if p == "exported" or c == "exported":
        return "exported"
    return "context"


def format_export_via(attribute: str, process: str) -> str:
    """Human-readable export pathway for hover text, e.g. ``VAR_FIn of TRADST00``."""
    attr = str(attribute or "").strip()
    proc = str(process or "").strip()
    if not attr or not proc:
        return ""
    return f"{attr} of {proc}"


def collapse_export_detail(
    prod_status: str,
    cons_status: str,
    prod_name: str,
    cons_name: str,
) -> str:
    """Describe which endpoint(s) drive PyPSA export on a collapsed link."""
    parts: list[str] = []
    p = str(prod_status or "context")
    c = str(cons_status or "context")
    if p in {"exported", "mixed", "double_count"}:
        via = format_export_via("VAR_FOut", prod_name)
        if via:
            parts.append(via)
    if c in {"exported", "mixed", "double_count"}:
        via = format_export_via("VAR_FIn", cons_name)
        if via:
            parts.append(via)
    return " and ".join(parts)


def _join_export_details(series) -> str:
    """Merge export_detail strings from several collapsed contributions."""
    seen: set[str] = set()
    out: list[str] = []
    for item in series:
        text = str(item or "").strip()
        if not text:
            continue
        for piece in text.split("; "):
            piece = piece.strip()
            if piece and piece not in seen:
                seen.add(piece)
                out.append(piece)
    return "; ".join(out)


def _merge_export_statuses_any(statuses: pd.Series) -> str:
    """groupby-compatible wrapper: collapse links use any-exported colouring."""
    return merge_export_statuses(statuses, any_exported=True)


def build_sankey_label_map(
    flows: pd.DataFrame,
    level: str,
    *,
    apply_netting: bool = False,
) -> pd.DataFrame:
    """
    Map aggregated Sankey node labels back to original TIMES process/commodity codes.

    Returns columns:
        side, sankey_label, times_code, times_description, mapping_label, value
    where ``mapping_label`` is the raw CSV aggregation value before inference fallbacks.
    """
    if flows.empty:
        return pd.DataFrame(
            columns=[
                "side",
                "sankey_label",
                "times_code",
                "times_description",
                "mapping_label",
                "value",
            ]
        )

    resolved = _resolve_agg_level(level)
    df = flows.copy()
    df = df[df["variable"].str.upper().isin(["VAR_FIN", "VAR_FOUT"])].copy()
    if df.empty:
        return pd.DataFrame(
            columns=[
                "side",
                "sankey_label",
                "times_code",
                "times_description",
                "mapping_label",
                "value",
            ]
        )

    # Attach the same labels used by aggregate_flows without collapsing rows
    if resolved == "L2" or level == "L2":
        df["process_node"] = df["process_code"].astype(str)
        df["commodity_node"] = df["commodity_code"].astype(str)
        df["_proc_map"] = df["process_code"].astype(str)
        df["_com_map"] = df["commodity_code"].astype(str)
    elif level == "L0":
        df["process_node"] = _nonempty_labels(df["sector"].map(_sector_label))
        df["process_node"] = df["process_node"].where(
            df["process_node"].ne(""), _process_identity_series(df)
        )
        if "commodity_sector" in df.columns:
            com_nodes = df.apply(
                lambda r: _sector_label(r["commodity_sector"])
                if str(r.get("commodity_sector") or "").strip()
                else _carrier_label(
                    r.get("pypsa_carrier", ""),
                    r.get("commodity", ""),
                    r.get("commodity_code", ""),
                ),
                axis=1,
            )
        else:
            com_nodes = _commodity_identity_series(df)
        df["commodity_node"] = _nonempty_labels(com_nodes)
        df["commodity_node"] = df["commodity_node"].where(
            df["commodity_node"].ne(""), _commodity_identity_series(df)
        )
        df["_proc_map"] = df["sector"] if "sector" in df.columns else ""
        df["_com_map"] = (
            df["commodity_sector"] if "commodity_sector" in df.columns else ""
        )
    else:
        pcol = proc_agg_col(resolved)
        ccol = com_agg_col(resolved)
        if pcol in df.columns and ccol in df.columns:
            _apply_column_agg_labels(df, resolved)
            df["_proc_map"] = _nonempty_labels(df[pcol])
            df["_com_map"] = _nonempty_labels(df[ccol])
        elif resolved == "Aggregation Level 2" or level == "mapping":
            _apply_legacy_mapping_labels(df)
            df["_proc_map"] = df.apply(_mapping_process_label, axis=1)
            df["_com_map"] = df.apply(_mapping_commodity_label, axis=1)
        else:
            raise ValueError(f"Cannot build label map for level {level!r}")

    proc = (
        df.groupby(
            ["process_node", "process_code", "process", "_proc_map"], dropna=False
        )["value"]
        .sum()
        .reset_index()
        .rename(
            columns={
                "process_node": "sankey_label",
                "process_code": "times_code",
                "process": "times_description",
                "_proc_map": "mapping_label",
            }
        )
    )
    proc.insert(0, "side", "process")

    com = (
        df.groupby(
            ["commodity_node", "commodity_code", "commodity", "_com_map"], dropna=False
        )["value"]
        .sum()
        .reset_index()
        .rename(
            columns={
                "commodity_node": "sankey_label",
                "commodity_code": "times_code",
                "commodity": "times_description",
                "_com_map": "mapping_label",
            }
        )
    )
    com.insert(0, "side", "commodity")

    out = pd.concat([proc, com], ignore_index=True)
    out = out.sort_values(
        ["side", "sankey_label", "value"], ascending=[True, True, False]
    ).reset_index(drop=True)
    if apply_netting:
        # Map is pre-netting by design (member inventory); flag kept for API symmetry.
        pass
    return out


def sankey_links_from_flows(
    flows: pd.DataFrame,
    *,
    flow_threshold: float = 0.0,
) -> pd.DataFrame:
    """
    Build Sankey link table: source → target with value.

    VAR_FIn  : commodity → process
    VAR_FOut : process → commodity

    Adds ``source_kind`` / ``target_kind`` in {process, commodity} so renderers can
    colour process and commodity nodes differently (and keep same-name nodes distinct).
    Adds ``export_status`` in {exported, context, mixed} when export tags exist.
    """
    empty_cols = [
        "source",
        "target",
        "source_kind",
        "target_kind",
        "value",
        "export_status",
        "exported",
        "matched_categories",
        "commodity",
    ]
    if flows.empty:
        return pd.DataFrame(columns=empty_cols)

    df = flows.copy()
    var_u = df["variable"].str.upper()
    is_fin = var_u == "VAR_FIN"
    df["source"] = df["commodity_code"].where(is_fin, df["process_code"])
    df["target"] = df["process_code"].where(is_fin, df["commodity_code"])
    df["source_kind"] = pd.Series(
        ["commodity" if fin else "process" for fin in is_fin], index=df.index
    )
    df["target_kind"] = pd.Series(
        ["process" if fin else "commodity" for fin in is_fin], index=df.index
    )
    if "commodity" in df.columns:
        df["commodity"] = df["commodity"].fillna(df["commodity_code"]).astype(str)
    else:
        df["commodity"] = df["commodity_code"].astype(str)

    agg_spec: dict = {"value": "sum"}
    status_source = None
    if "exported" in df.columns:
        df["exported"] = df["exported"].fillna(False).astype(bool)
        status_source = "exported"
    elif "export_status" in df.columns:
        status_source = "export_status"

    if "matched_categories" in df.columns:

        def _join_cats(series):
            cats: set[str] = set()
            for item in series:
                if isinstance(item, list):
                    cats.update(item)
                elif isinstance(item, str) and item:
                    cats.update(x for x in item.split("|") if x)
            return "|".join(sorted(cats))

        agg_spec["matched_categories"] = _join_cats

    def _join_commodities(series):
        return "|".join(sorted({str(x) for x in series if str(x).strip()}))

    agg_spec["commodity"] = _join_commodities

    group_cols = ["source", "target", "source_kind", "target_kind"]
    links = df.groupby(group_cols, as_index=False).agg(agg_spec)

    if status_source == "exported":
        status = (
            df.groupby(group_cols)["exported"]
            .apply(classify_export_status)
            .reset_index(name="export_status")
        )
        links = links.merge(status, on=group_cols, how="left")
    elif status_source == "export_status":
        status = (
            df.groupby(group_cols)["export_status"]
            .apply(merge_export_statuses)
            .reset_index(name="export_status")
        )
        links = links.merge(status, on=group_cols, how="left")

    if "export_status" in links.columns:
        links["export_status"] = links["export_status"].fillna("context")
        links["exported"] = links["export_status"].eq("exported")

    if flow_threshold > 0:
        links = links[links["value"] > flow_threshold]
    return links


def commodity_fin_fout_balance(
    flows: pd.DataFrame,
    *,
    abs_tol: float = 1e-6,
) -> pd.DataFrame:
    """
    Compare Σ VAR_FOut vs Σ VAR_FIn for each commodity hub.

    Returns columns:
        commodity_code, fout, fin, residual, rel_err, balanced
    """
    empty = pd.DataFrame(
        columns=["commodity_code", "fout", "fin", "residual", "rel_err", "balanced"]
    )
    if flows.empty:
        return empty

    df = flows[flows["variable"].str.upper().isin(["VAR_FIN", "VAR_FOUT"])].copy()
    if df.empty:
        return empty

    fout = (
        df[df["variable"].str.upper() == "VAR_FOUT"]
        .groupby("commodity_code")["value"]
        .sum()
    )
    fin = (
        df[df["variable"].str.upper() == "VAR_FIN"]
        .groupby("commodity_code")["value"]
        .sum()
    )
    codes = sorted(set(fout.index) | set(fin.index))
    rows = []
    for code in codes:
        fo = float(fout.get(code, 0.0))
        fi = float(fin.get(code, 0.0))
        residual = fo - fi
        scale = max(abs(fo), abs(fi), abs_tol)
        rel_err = abs(residual) / scale
        rows.append(
            {
                "commodity_code": code,
                "fout": fo,
                "fin": fi,
                "residual": residual,
                "rel_err": rel_err,
                "balanced": abs(residual) <= abs_tol,
            }
        )
    return pd.DataFrame(rows)


class CommodityImbalanceError(ValueError):
    """Raised when a commodity hub has relative FIN/FOUT imbalance ≥ threshold."""

    def __init__(self, message: str, balance: pd.DataFrame):
        super().__init__(message)
        self.balance = balance


def _join_category_values(values) -> str:
    cats: set[str] = set()
    for item in values:
        if isinstance(item, list):
            cats.update(str(x) for x in item if x)
        elif isinstance(item, str) and item:
            cats.update(x for x in item.split("|") if x)
    return "|".join(sorted(cats))


def imbalance_process_label(
    commodity: str,
    *,
    fout: float,
    fin: float,
    rel_err: float,
    warn_rel: float = 0.10,
) -> str:
    """
    Artificial process-node name for *unexplained* imbalances.

    Examples:
        Unbalanced ≥10%: Oil products (FOut>FIn, 78.9%)
        Unbalanced <10%: Electricity (FOut>FIn, 3.6%)
    """
    direction = "FOut>FIn" if fout >= fin else "FIn>FOut"
    pct = 100.0 * float(rel_err)
    if rel_err >= warn_rel:
        return f"Unbalanced ≥{100.0 * warn_rel:.0f}%: {commodity} ({direction}, {pct:.1f}%)"
    return f"Unbalanced <{100.0 * warn_rel:.0f}%: {commodity} ({direction}, {pct:.1f}%)"


def is_pj_activity_unit(unit: str | None) -> bool:
    return str(unit or "").strip().upper() == "PJ"


def excluded_non_pj_consumers(
    reference_flows: pd.DataFrame,
    commodity_code: str,
    *,
    process_activity_units: dict[str, str],
    abs_tol: float = 1e-6,
) -> pd.DataFrame:
    """
    VAR_FIn consumers of ``commodity_code`` whose process Activity unit is not PJ.

    Only processes present in ``process_activity_units`` are considered. Aggregated
    Sankey labels (absent from that map) are ignored — otherwise every unknown code
    would look like a non-PJ exclusion.

    Returns columns: process_code, process, value, activity_unit.
    """
    empty = pd.DataFrame(
        columns=["process_code", "process", "value", "activity_unit"]
    )
    if reference_flows.empty or not process_activity_units:
        return empty
    df = reference_flows[
        (reference_flows["commodity_code"].astype(str) == str(commodity_code))
        & (reference_flows["variable"].str.upper() == "VAR_FIN")
    ].copy()
    if df.empty:
        return empty
    rows: list[dict] = []
    for proc, sub in df.groupby("process_code", sort=False):
        if str(proc) not in process_activity_units:
            continue
        unit = process_activity_units[str(proc)]
        if is_pj_activity_unit(unit):
            continue
        proc_label = (
            str(sub["process"].iloc[0])
            if "process" in sub.columns and len(sub)
            else str(proc)
        )
        value = float(sub["value"].sum())
        if value <= abs_tol:
            continue
        rows.append(
            {
                "process_code": str(proc),
                "process": proc_label if proc_label.strip() else str(proc),
                "value": value,
                "activity_unit": str(unit or "").strip() or "?",
            }
        )
    if not rows:
        return empty
    return pd.DataFrame(rows).sort_values("value", ascending=False).reset_index(drop=True)


def _reference_side_outside_view(
    reference_flows: pd.DataFrame,
    commodity_code: str,
    *,
    variable: str,
    present_processes: set[str],
    abs_tol: float = 1e-6,
) -> pd.DataFrame:
    """Reference FIN/FOUT rows for a commodity whose process is absent from the view."""
    empty = pd.DataFrame(columns=["process_code", "process", "value"])
    if reference_flows is None or reference_flows.empty:
        return empty
    df = reference_flows[
        (reference_flows["commodity_code"].astype(str) == str(commodity_code))
        & (reference_flows["variable"].str.upper() == variable.upper())
    ].copy()
    if df.empty:
        return empty
    rows: list[dict] = []
    for proc, sub in df.groupby("process_code", sort=False):
        if str(proc) in present_processes:
            continue
        value = float(sub["value"].sum())
        if value <= abs_tol:
            continue
        proc_label = (
            str(sub["process"].iloc[0])
            if "process" in sub.columns and len(sub)
            else str(proc)
        )
        rows.append(
            {
                "process_code": str(proc),
                "process": proc_label if proc_label.strip() else str(proc),
                "value": value,
            }
        )
    if not rows:
        return empty
    return pd.DataFrame(rows).sort_values("value", ascending=False).reset_index(drop=True)


def _residual_sink_plan(
    *,
    com_label: str,
    commodity_code: str,
    f_out: float,
    f_in: float,
    rel_err: float,
    producers: pd.DataFrame,
    consumers: pd.DataFrame,
    warn_rel: float,
    abs_tol: float,
    reference_flows: pd.DataFrame | None,
    process_activity_units: dict[str, str] | None,
) -> dict:
    """
    Decide how to label/log a commodity residual (FOut≠FIn).

    Returns dict with keys:
        expected (bool), class (str), label (str), tooltip (str),
        excluded (DataFrame|None) — non-PJ consumers when attributable.
    """
    residual = f_out - f_in
    slack = max(abs_tol, 1e-3 * max(abs(f_out), abs(f_in), 1.0))
    unexplained = {
        "expected": False,
        "class": "unexplained",
        "label": imbalance_process_label(
            com_label,
            fout=f_out,
            fin=f_in,
            rel_err=rel_err,
            warn_rel=warn_rel,
        ),
        "tooltip": (
            f"Unexplained commodity imbalance for '{com_label}': "
            f"ΣFOut={f_out:.4g} PJ, ΣFIn={f_in:.4g} PJ "
            f"(rel_err={100.0 * rel_err:.1f}%). "
            "Residual energy has no matching process on the opposite side."
        ),
        "excluded": None,
    }

    present_prod = (
        set(producers["process_code"].astype(str)) if not producers.empty else set()
    )
    present_cons = (
        set(consumers["process_code"].astype(str)) if not consumers.empty else set()
    )

    # FOut-only hub: typical TIMES DEM output (final demand), not an accounting error.
    # When ``reference_flows`` is provided (e.g. full system vs export neighbourhood),
    # require FIn≈0 there too — otherwise missing consumers are a view artifact.
    if residual > abs_tol and f_in <= abs_tol:
        ref_fin = 0.0
        if reference_flows is not None and not reference_flows.empty:
            ref = reference_flows[
                (reference_flows["commodity_code"].astype(str) == str(commodity_code))
                & (reference_flows["variable"].str.upper() == "VAR_FIN")
            ]
            ref_fin = float(ref["value"].sum()) if not ref.empty else 0.0
        if ref_fin <= abs_tol:
            if not producers.empty and len(producers) == 1:
                proc_name = str(producers.iloc[0]["process_code"])
            elif not producers.empty:
                # Dominant producer name when one process carries ≥90% of FOut.
                top = producers.sort_values("value", ascending=False).iloc[0]
                if float(top["value"]) >= 0.9 * f_out - abs_tol:
                    proc_name = str(top["process_code"])
                else:
                    proc_name = com_label
            else:
                proc_name = com_label
            return {
                "expected": True,
                "class": "final_demand",
                "label": proc_name,
                "tooltip": (
                    f"Final energy demand sink for commodity '{com_label}'. "
                    "No process consumes this commodity in the energy Sankey "
                    "(typical TIMES DEM / end-use output). "
                    f"Node '{proc_name}' is the demand-side process (or demand label); "
                    "magenta marks a non-energy accounting endpoint, not a conversion tech."
                ),
                "excluded": None,
            }

    # Missing FIN explained by non-PJ consumers dropped upstream of this frame.
    if (
        residual > abs_tol
        and reference_flows is not None
        and process_activity_units
    ):
        excluded = excluded_non_pj_consumers(
            reference_flows,
            str(commodity_code),
            process_activity_units=process_activity_units,
            abs_tol=abs_tol,
        )
        excl_sum = float(excluded["value"].sum()) if not excluded.empty else 0.0
        # Allow small numerical slack; residual should match excluded FIN mass.
        if excl_sum > abs_tol and abs(excl_sum - residual) <= slack:
            if len(excluded) == 1:
                proc_name = str(excluded.iloc[0]["process"])
                unit = str(excluded.iloc[0]["activity_unit"])
                tip = (
                    f"Energy delivered to process '{proc_name}' via commodity "
                    f"'{com_label}'. That process is excluded from the energy "
                    f"Sankey because its Activity unit is '{unit}' (not PJ). "
                    "Magenta marks the non-energy demand endpoint."
                )
            else:
                # Prefer a single shared process display name when all match.
                names = excluded["process"].astype(str)
                if names.nunique() == 1:
                    proc_name = str(names.iloc[0])
                else:
                    # Dominant consumer by energy; keep label short.
                    proc_name = str(
                        excluded.sort_values("value", ascending=False).iloc[0]["process"]
                    )
                units = sorted({str(u) for u in excluded["activity_unit"].unique()})
                tip = (
                    f"Energy delivered to {len(excluded)} non-PJ consumer process(es) "
                    f"via commodity '{com_label}' (Activity unit "
                    f"{', '.join(units)}). Those processes are excluded from the "
                    "energy Sankey; magenta marks the non-energy demand endpoint. "
                    f"Shown as '{proc_name}'."
                )
            return {
                "expected": True,
                "class": "excluded_consumer",
                "label": proc_name,
                "tooltip": tip,
                "excluded": excluded,
            }

    # Export-neighbourhood (or other) view truncation: the *full* reference hub is
    # approximately balanced, but this subset omits producers and/or consumers.
    if reference_flows is not None and abs(residual) > abs_tol:
        omitted_cons = _reference_side_outside_view(
            reference_flows,
            str(commodity_code),
            variable="VAR_FIN",
            present_processes=present_cons,
            abs_tol=abs_tol,
        )
        omitted_prod = _reference_side_outside_view(
            reference_flows,
            str(commodity_code),
            variable="VAR_FOUT",
            present_processes=present_prod,
            abs_tol=abs_tol,
        )
        omitted_fin = (
            float(omitted_cons["value"].sum()) if not omitted_cons.empty else 0.0
        )
        omitted_fout = (
            float(omitted_prod["value"].sum()) if not omitted_prod.empty else 0.0
        )
        if omitted_fin > abs_tol or omitted_fout > abs_tol:
            ref = reference_flows[
                reference_flows["commodity_code"].astype(str) == str(commodity_code)
            ]
            if not ref.empty:
                ref_fout = float(
                    ref.loc[ref["variable"].str.upper() == "VAR_FOUT", "value"].sum()
                )
                ref_fin = float(
                    ref.loc[ref["variable"].str.upper() == "VAR_FIN", "value"].sum()
                )
                ref_scale = max(abs(ref_fout), abs(ref_fin), abs_tol)
                ref_residual = ref_fout - ref_fin
                ref_rel = abs(ref_residual) / ref_scale
                expected_view_residual = ref_residual + omitted_fin - omitted_fout
                # Full system roughly balanced; view residual matches omitted sides.
                if (
                    ref_rel < warn_rel
                    and abs(expected_view_residual - residual) <= slack
                ):
                    if residual >= 0:
                        outside = (
                            omitted_cons if not omitted_cons.empty else omitted_prod
                        )
                        role = "consumer" if not omitted_cons.empty else "producer"
                    else:
                        outside = (
                            omitted_prod if not omitted_prod.empty else omitted_cons
                        )
                        role = "producer" if not omitted_prod.empty else "consumer"
                    proc_name = (
                        str(outside.iloc[0]["process"])
                        if not outside.empty
                        else com_label
                    )
                    tip = (
                        f"View truncation for commodity '{com_label}': "
                        f"the full system is nearly balanced "
                        f"(ΣFOut={ref_fout:.4g} / ΣFIn={ref_fin:.4g}), "
                        f"but this Sankey subset omits {len(omitted_cons)} consumer(s) "
                        f"({omitted_fin:.4g} PJ) and {len(omitted_prod)} producer(s) "
                        f"({omitted_fout:.4g} PJ) — typical of the export neighbourhood. "
                        f"Dominant omitted {role}: '{proc_name}'. "
                        "Not a TIMES accounting error."
                    )
                    return {
                        "expected": True,
                        "class": "neighbourhood_truncation",
                        "label": proc_name,
                        "tooltip": tip,
                        "excluded": None,
                    }

    return unexplained


def collapse_commodity_nodes(
    flows: pd.DataFrame,
    *,
    flow_threshold: float = 0.0,
    warn_rel: float = 0.10,
    abs_tol: float = 1e-6,
    raise_on_imbalance: bool = False,
    reference_flows: pd.DataFrame | None = None,
    process_activity_units: dict[str, str] | None = None,
) -> pd.DataFrame:
    """
    Remove commodity hubs so Sankey links run process → process.

    For each commodity ``C`` with producers (VAR_FOut) and consumers (VAR_FIn):

    - If ΣFOut ≈ ΣFIn: allocate flows proportionally and drop ``C``.
    - If the residual is an *expected* sink (final-demand FOut-only commodity, or
      FIN only on non-PJ processes visible in ``reference_flows``): route residual
      to a magenta node named after the demand process, log at INFO (not error).
    - If relative imbalance is unexplained and ``< warn_rel``: log a warning and
      route residual to an ``Unbalanced <…`` magenta node.
    - If unexplained and ``≥ warn_rel``: log an error (and optionally raise
      :class:`CommodityImbalanceError`), still collapsing matched energy.

    Pass ``reference_flows`` + ``process_activity_units`` (Process → Activity unit)
    when non-PJ consumers may have been filtered out of ``flows`` before collapse.

    Link colours still use ``export_status`` (exported / mixed / context).
    Collapsed links carry a ``commodity`` label for hover text; imbalance links
    also carry ``imbalance_class`` and ``imbalance_tooltip``.
    """
    empty_cols = [
        "source",
        "target",
        "source_kind",
        "target_kind",
        "value",
        "export_status",
        "exported",
        "matched_categories",
        "commodity",
        "export_detail",
        "imbalance_class",
        "imbalance_tooltip",
    ]
    if flows.empty:
        return pd.DataFrame(columns=empty_cols)

    balance = commodity_fin_fout_balance(flows, abs_tol=abs_tol)
    if balance.empty:
        return pd.DataFrame(columns=empty_cols)

    bal_by_code = balance.set_index("commodity_code")
    # String keys for O(1) lookup regardless of original index dtype.
    bal_by_code.index = bal_by_code.index.map(str)

    df = flows[flows["variable"].str.upper().isin(["VAR_FIN", "VAR_FOUT"])].copy()
    if "commodity" not in df.columns:
        df["commodity"] = df["commodity_code"].astype(str)
    else:
        df["commodity"] = df["commodity"].fillna(df["commodity_code"]).astype(str)
    if "matched_categories" not in df.columns:
        df["matched_categories"] = ""
    if "exported" in df.columns:
        df["exported"] = df["exported"].fillna(False).astype(bool)
    elif "export_status" in df.columns:
        df["exported"] = df["export_status"].map(
            lambda s: str(s) in {"exported", "mixed"}
        )
    else:
        df["exported"] = False
        df["export_status"] = "context"

    # Pre-index by commodity once (avoids repeated full-frame scans).
    df["_var"] = df["variable"].astype(str).str.upper()
    groups_by_code = {
        str(code): grp for code, grp in df.groupby(df["commodity_code"].astype(str), sort=False)
    }

    def _agg_process_side(side: pd.DataFrame) -> list[dict]:
        """Aggregate one FIN/FOUT side to per-process dicts (no DataFrame.iterrows)."""
        if side.empty:
            return []
        rows: list[dict] = []
        for proc, sub in side.groupby("process_code", sort=False):
            exp = sub["exported"].fillna(False).astype(bool)
            has_exp = bool(exp.any())
            has_non = bool((~exp).any())
            if has_exp and has_non:
                status = "mixed"
            elif has_exp:
                status = "exported"
            else:
                status = "context"
            rows.append(
                {
                    "process_code": proc,
                    "value": float(sub["value"].sum()),
                    "export_status": status,
                    "matched_categories": _join_category_values(sub["matched_categories"]),
                }
            )
        return rows

    # Pre-compute sink plans so logging matches the labels used on links.
    sink_plans: dict[str, dict] = {}
    unexplained_err_codes: list[str] = []
    for code, row in bal_by_code.iterrows():
        if bool(row["balanced"]):
            continue
        code_s = str(code)
        grp = groups_by_code.get(code_s)
        if grp is None or grp.empty:
            com_label = code_s
            producers: list[dict] = []
            consumers: list[dict] = []
        else:
            com_label = str(grp["commodity"].iloc[0]) if len(grp) else code_s
            producers = _agg_process_side(grp[grp["_var"] == "VAR_FOUT"])
            consumers = _agg_process_side(grp[grp["_var"] == "VAR_FIN"])
        plan = _residual_sink_plan(
            com_label=com_label,
            commodity_code=code_s,
            f_out=float(row["fout"]),
            f_in=float(row["fin"]),
            rel_err=float(row["rel_err"]),
            producers=pd.DataFrame(producers) if producers else pd.DataFrame(
                columns=["process_code", "value"]
            ),
            consumers=pd.DataFrame(consumers) if consumers else pd.DataFrame(
                columns=["process_code", "value"]
            ),
            warn_rel=warn_rel,
            abs_tol=abs_tol,
            reference_flows=reference_flows,
            process_activity_units=process_activity_units,
        )
        sink_plans[code_s] = plan
        if plan["expected"]:
            logger.info(
                "Commodity hub %r residual is expected (%s): FOut=%.4g, FIn=%.4g; "
                "routing to magenta sink %r.",
                code_s,
                plan["class"],
                float(row["fout"]),
                float(row["fin"]),
                plan["label"],
            )
        elif float(row["rel_err"]) < warn_rel:
            logger.warning(
                "Commodity hub %r is unbalanced (rel_err=%.1f%%, FOut=%.4g, FIn=%.4g); "
                "collapsing matched flows and routing residual to %r.",
                code_s,
                100.0 * float(row["rel_err"]),
                float(row["fout"]),
                float(row["fin"]),
                plan["label"],
            )
        else:
            unexplained_err_codes.append(code_s)
            logger.error(
                "Commodity hub %r is unbalanced by ≥%.0f%% (rel_err=%.1f%%, "
                "FOut=%.4g, FIn=%.4g); collapsing matched flows anyway and routing "
                "residual to %r.",
                code_s,
                100.0 * warn_rel,
                100.0 * float(row["rel_err"]),
                float(row["fout"]),
                float(row["fin"]),
                plan["label"],
            )

    if raise_on_imbalance and unexplained_err_codes:
        worst = (
            bal_by_code.loc[unexplained_err_codes]
            .sort_values("rel_err", ascending=False)
            .iloc[0]
        )
        raise CommodityImbalanceError(
            f"Commodity hub {worst.name!r} unbalanced by "
            f"{100.0 * float(worst['rel_err']):.1f}% "
            f"(threshold {100.0 * warn_rel:.0f}%).",
            balance=balance,
        )

    link_rows: list[dict] = []

    for code_s, grp in groups_by_code.items():
        com_label = str(grp["commodity"].iloc[0]) if len(grp) else code_s
        producers = _agg_process_side(grp[grp["_var"] == "VAR_FOUT"])
        consumers = _agg_process_side(grp[grp["_var"] == "VAR_FIN"])

        f_out = sum(p["value"] for p in producers)
        f_in = sum(c["value"] for c in consumers)

        if f_out <= abs_tol and f_in <= abs_tol:
            continue

        bal = bal_by_code.loc[code_s] if code_s in bal_by_code.index else None
        rel_err = float(bal["rel_err"]) if bal is not None else 0.0
        plan = sink_plans.get(code_s)
        if plan is None and abs(f_out - f_in) > abs_tol:
            plan = _residual_sink_plan(
                com_label=com_label,
                commodity_code=code_s,
                f_out=f_out,
                f_in=f_in,
                rel_err=rel_err,
                producers=pd.DataFrame(producers) if producers else pd.DataFrame(
                    columns=["process_code", "value"]
                ),
                consumers=pd.DataFrame(consumers) if consumers else pd.DataFrame(
                    columns=["process_code", "value"]
                ),
                warn_rel=warn_rel,
                abs_tol=abs_tol,
                reference_flows=reference_flows,
                process_activity_units=process_activity_units,
            )
        artificial = str(plan["label"]) if plan else imbalance_process_label(
            com_label,
            fout=f_out,
            fin=f_in,
            rel_err=rel_err,
            warn_rel=warn_rel,
        )
        imb_class = str(plan["class"]) if plan else ""
        imb_tip = str(plan["tooltip"]) if plan else ""

        transferable = min(f_out, f_in)
        if transferable > abs_tol and f_out > abs_tol and f_in > abs_tol:
            scale_out = transferable / f_out
            inv_in = 1.0 / f_in
            for prod in producers:
                transfer_p = float(prod["value"]) * scale_out
                if transfer_p <= abs_tol:
                    continue
                p_code = str(prod["process_code"])
                p_stat = str(prod["export_status"])
                p_cats = prod["matched_categories"]
                for cons in consumers:
                    c_code = str(cons["process_code"])
                    if p_code == c_code:
                        # Plotly Sankey cannot draw self-loops; drop internal recycle.
                        continue
                    value = transfer_p * (float(cons["value"]) * inv_in)
                    if value <= abs_tol:
                        continue
                    c_stat = str(cons["export_status"])
                    status = collapse_pair_export_status(p_stat, c_stat)
                    detail = collapse_export_detail(p_stat, c_stat, p_code, c_code)
                    cats = _join_category_values([p_cats, cons["matched_categories"]])
                    link_rows.append(
                        {
                            "source": p_code,
                            "target": c_code,
                            "source_kind": "process",
                            "target_kind": "process",
                            "value": value,
                            "export_status": status,
                            "exported": status in {"exported", "double_count"},
                            "matched_categories": cats,
                            "commodity": com_label,
                            "export_detail": detail,
                            "imbalance_class": "",
                            "imbalance_tooltip": "",
                        }
                    )

        # Residual → magenta sink (expected demand process or unexplained label).
        if f_out > f_in + abs_tol and producers:
            scale_resid = (f_out - transferable) / f_out
            for prod in producers:
                value = float(prod["value"]) * scale_resid
                if value <= abs_tol:
                    continue
                pstat = str(prod["export_status"])
                p_code = str(prod["process_code"])
                link_rows.append(
                    {
                        "source": p_code,
                        "target": artificial,
                        "source_kind": "process",
                        "target_kind": "imbalance",
                        "value": value,
                        "export_status": pstat,
                        "exported": pstat in {"exported", "double_count"},
                        "matched_categories": prod["matched_categories"],
                        "commodity": com_label,
                        "export_detail": (
                            format_export_via("VAR_FOut", p_code)
                            if pstat in {"exported", "mixed", "double_count"}
                            else ""
                        ),
                        "imbalance_class": imb_class,
                        "imbalance_tooltip": imb_tip,
                    }
                )
        elif f_in > f_out + abs_tol and consumers:
            scale_resid = (f_in - transferable) / f_in
            for cons in consumers:
                value = float(cons["value"]) * scale_resid
                if value <= abs_tol:
                    continue
                cstat = str(cons["export_status"])
                c_code = str(cons["process_code"])
                link_rows.append(
                    {
                        "source": artificial,
                        "target": c_code,
                        "source_kind": "imbalance",
                        "target_kind": "process",
                        "value": value,
                        "export_status": cstat,
                        "exported": cstat in {"exported", "double_count"},
                        "matched_categories": cons["matched_categories"],
                        "commodity": com_label,
                        "export_detail": (
                            format_export_via("VAR_FIn", c_code)
                            if cstat in {"exported", "mixed", "double_count"}
                            else ""
                        ),
                        "imbalance_class": imb_class,
                        "imbalance_tooltip": imb_tip,
                    }
                )

    if not link_rows:
        return pd.DataFrame(columns=empty_cols)

    links = pd.DataFrame(link_rows)
    group_cols = ["source", "target", "source_kind", "target_kind"]

    def _first_nonempty(series: pd.Series) -> str:
        for item in series:
            text = str(item or "").strip()
            if text:
                return text
        return ""

    def _join_commodity_labels(series: pd.Series) -> str:
        return "|".join(sorted({str(x) for x in series}))

    agg = (
        links.groupby(group_cols, as_index=False, sort=False)
        .agg(
            value=("value", "sum"),
            matched_categories=("matched_categories", _join_category_values),
            commodity=("commodity", _join_commodity_labels),
            export_status=("export_status", _merge_export_statuses_any),
            export_detail=("export_detail", _join_export_details),
            imbalance_class=("imbalance_class", _first_nonempty),
            imbalance_tooltip=("imbalance_tooltip", _first_nonempty),
        )
    )
    agg["exported"] = agg["export_status"].isin(["exported", "double_count"])
    if flow_threshold > 0:
        agg = agg[agg["value"] > flow_threshold]
    return agg.reset_index(drop=True)


def net_collapsed_process_links(links: pd.DataFrame) -> pd.DataFrame:
    """
    Net reciprocal process↔process ribbons after commodity-hub collapse.

    Aggregation can place related energy on both A→B and B→A (e.g. fuel-tech
    grid FIn and a context electricity FOut into the power pool). Keep the net
    direction only. Links that touch imbalance nodes are left unchanged.
    """
    if links is None or links.empty:
        return links
    required = {"source", "target", "value", "source_kind", "target_kind"}
    if not required.issubset(links.columns):
        return links

    is_proc_pair = (links["source_kind"].astype(str) == "process") & (
        links["target_kind"].astype(str) == "process"
    )
    proc = links.loc[is_proc_pair]
    other = links.loc[~is_proc_pair]
    if proc.empty:
        return links.reset_index(drop=True)

    # Directed totals via plain dicts (avoids O(n²) DataFrame boolean scans).
    directed_vals: dict[tuple[str, str], float] = {}
    sources = proc["source"].astype(str).tolist()
    targets = proc["target"].astype(str).tolist()
    values = proc["value"].tolist()
    for a, b, v in zip(sources, targets, values):
        directed_vals[(a, b)] = directed_vals.get((a, b), 0.0) + float(v)

    # Representative metadata row per directed edge (largest value wins).
    meta_map: dict[tuple[str, str], dict] = {}
    status_map: dict[tuple[str, str], str] = {}
    commodity_map: dict[tuple[str, str], set[str]] = {}
    records = proc.to_dict("records")
    # Sort by value descending so first write is the max-value row.
    records.sort(key=lambda r: float(r.get("value") or 0.0), reverse=True)
    for row in records:
        key = (str(row["source"]), str(row["target"]))
        if key not in meta_map:
            meta_map[key] = dict(row)
            status_map[key] = str(row.get("export_status", "context") or "context")
        labels = commodity_map.setdefault(key, set())
        for part in str(row.get("commodity") or "").split("|"):
            part = part.strip()
            if part:
                labels.add(part)

    seen: set[tuple[str, str]] = set()
    keep_rows: list[dict] = []
    for (a, b), fv in directed_vals.items():
        if a == b:
            continue
        canon = (a, b) if a < b else (b, a)
        if canon in seen:
            continue
        seen.add(canon)

        rv = float(directed_vals.get((b, a), 0.0))
        net = fv - rv
        if abs(net) < 1e-12:
            continue
        if net > 0:
            src, tgt, val = a, b, net
        else:
            src, tgt, val = b, a, -net

        base = meta_map.get((src, tgt)) or meta_map.get((tgt, src))
        if base is None:
            continue
        row = dict(base)
        row["source"] = src
        row["target"] = tgt
        row["value"] = val
        # Merge export status from both directions when both existed
        if fv > 0 and rv > 0 and "export_status" in row:
            statuses = []
            for key in ((a, b), (b, a)):
                if key in status_map:
                    statuses.append(status_map[key])
            row["export_status"] = merge_export_statuses(
                pd.Series(statuses), any_exported=True
            )
            row["exported"] = str(row["export_status"]) in {
                "exported",
                "double_count",
            }
        # Combine commodity labels
        labels: set[str] = set()
        for key in ((a, b), (b, a)):
            labels.update(commodity_map.get(key, ()))
        if labels:
            row["commodity"] = "|".join(sorted(labels))
        keep_rows.append(row)

    netted = pd.DataFrame(keep_rows) if keep_rows else proc.iloc[0:0].copy()
    if other.empty:
        return netted.reset_index(drop=True)
    if netted.empty:
        return other.reset_index(drop=True)
    cols = list(dict.fromkeys(list(netted.columns) + list(other.columns)))
    netted = netted.copy()
    other = other.copy()
    for frame in (netted, other):
        for c in cols:
            if c not in frame.columns:
                frame[c] = 0.0 if c in {"value", "exported"} else ""
    return pd.concat([netted[cols], other[cols]], ignore_index=True)
