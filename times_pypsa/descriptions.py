"""Load TIMES process/commodity dictionaries for human-readable descriptions."""

from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def resolve_data_dir(mappings_dir: Path | str) -> Path | None:
    """Locate the repo ``data/`` directory that holds AllProcesses / AllCommodities."""
    mappings_dir = Path(mappings_dir)
    candidates = [
        mappings_dir.parent.parent / "data",  # times_pypsa/mappings → repo/data
        mappings_dir.parent / "data",
        mappings_dir / "data",
        Path.cwd() / "data",
    ]
    for path in candidates:
        if (path / "AllCommodities.csv").exists() or (path / "AllProcesses.csv").exists():
            return path
    return None


def _read_semicolon_table(path: Path) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path, sep=";", engine="python")


def load_commodity_descriptions(data_dir: Path | str | None) -> dict[str, str]:
    """Map TIMES commodity code → Description from ``AllCommodities.csv``."""
    if data_dir is None:
        return {}
    path = Path(data_dir) / "AllCommodities.csv"
    raw = _read_semicolon_table(path)
    if raw.empty or "Name" not in raw.columns:
        return {}
    out: dict[str, str] = {}
    for _, row in raw.iterrows():
        code = str(row.get("Name") or "").strip()
        if not code or code.lower() == "nan":
            continue
        desc = str(row.get("Description") or "").strip()
        if desc.lower() in {"", "nan"}:
            desc = code
        # Prefer first non-trivial description if duplicates exist
        if code not in out or (out[code] == code and desc != code):
            out[code] = desc
    logger.info("Loaded %d commodity descriptions from %s", len(out), path)
    return out


def load_process_descriptions(data_dir: Path | str | None) -> dict[str, dict[str, str]]:
    """
    Map TIMES process code → {description, sector, type} from ``AllProcesses.csv``.
    """
    if data_dir is None:
        return {}
    path = Path(data_dir) / "AllProcesses.csv"
    raw = _read_semicolon_table(path)
    if raw.empty or "Name" not in raw.columns:
        return {}
    out: dict[str, dict[str, str]] = {}
    for _, row in raw.iterrows():
        code = str(row.get("Name") or "").strip()
        if not code or code.lower() == "nan":
            continue
        desc = str(row.get("Description") or "").strip()
        if desc.lower() in {"", "nan"}:
            desc = code
        sector = str(row.get("Sector") or "").strip()
        if sector.lower() == "nan":
            sector = ""
        ptype = str(row.get("Type") or "").strip()
        if ptype.lower() == "nan":
            ptype = ""
        if code not in out or (out[code]["description"] == code and desc != code):
            out[code] = {"description": desc, "sector": sector, "type": ptype}
    logger.info("Loaded %d process descriptions from %s", len(out), path)
    return out


def fill_flow_descriptions(
    flows: pd.DataFrame,
    *,
    commodity_descriptions: dict[str, str] | None = None,
    process_info: dict[str, dict[str, str]] | None = None,
) -> pd.DataFrame:
    """Replace missing / code-only process and commodity names with dictionary text."""
    if flows.empty:
        return flows
    df = flows.copy()
    commodity_descriptions = commodity_descriptions or {}
    process_info = process_info or {}

    if commodity_descriptions and "commodity_code" in df.columns:
        mapped = df["commodity_code"].map(
            lambda c: commodity_descriptions.get(str(c).strip(), "")
        )
        current = df.get("commodity", pd.Series("", index=df.index)).fillna("").astype(str)
        code = df["commodity_code"].astype(str)
        use_mapped = mapped.astype(str).str.strip().ne("") & (
            current.str.strip().isin(["", "nan"]) | current.eq(code)
        )
        df["commodity"] = current.where(~use_mapped, mapped)

    if process_info and "process_code" in df.columns:
        mapped = df["process_code"].map(
            lambda p: process_info.get(str(p).strip(), {}).get("description", "")
        )
        current = df.get("process", pd.Series("", index=df.index)).fillna("").astype(str)
        code = df["process_code"].astype(str)
        use_mapped = mapped.astype(str).str.strip().ne("") & (
            current.str.strip().isin(["", "nan"]) | current.eq(code)
        )
        df["process"] = current.where(~use_mapped, mapped)

        if "sector" in df.columns:
            sector_mapped = df["process_code"].map(
                lambda p: process_info.get(str(p).strip(), {}).get("sector", "")
            )
            cur_sector = df["sector"].fillna("").astype(str)
            fill = cur_sector.str.strip().isin(["", "nan"]) & sector_mapped.astype(str).str.strip().ne(
                ""
            )
            df["sector"] = cur_sector.where(~fill, sector_mapped)

        if "process_type" in df.columns:
            type_mapped = df["process_code"].map(
                lambda p: process_info.get(str(p).strip(), {}).get("type", "")
            )
            cur_type = df["process_type"].fillna("").astype(str)
            fill = cur_type.str.strip().isin(["", "nan"]) & type_mapped.astype(str).str.strip().ne("")
            df["process_type"] = cur_type.where(~fill, type_mapped)

    return df
