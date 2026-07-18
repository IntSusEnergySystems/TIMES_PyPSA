"""Parse TIMES .vdt topology sidecars (process–commodity IN/OUT links)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import pandas as pd

logger = logging.getLogger(__name__)


def _parse_vdt_line(line: str) -> list[str]:
    """Quote-aware CSV split (same convention as .vd files)."""
    parts: list[str] = []
    current = ""
    in_quotes = False
    for char in line.strip():
        if char == '"':
            in_quotes = not in_quotes
        elif char == "," and not in_quotes:
            parts.append(current.strip('"'))
            current = ""
        else:
            current += char
    parts.append(current.strip('"'))
    return parts


@dataclass
class Topology:
    """Static TIMES process–commodity wiring from a .vdt file."""

    links: pd.DataFrame  # region, process, commodity, direction (IN|OUT)
    source_path: Path | None = None

    @property
    def n_links(self) -> int:
        return len(self.links)

    def as_key_set(self) -> set[tuple[str, str, str, str]]:
        """Return (region, process, commodity, direction) keys."""
        if self.links.empty:
            return set()
        return set(
            zip(
                self.links["region"].astype(str),
                self.links["process"].astype(str),
                self.links["commodity"].astype(str),
                self.links["direction"].astype(str).str.upper(),
            )
        )

    def direction_for_variable(self, variable: str) -> str | None:
        """Map VAR_FIn / VAR_FOut to topology direction."""
        vu = str(variable).upper()
        if vu == "VAR_FIN":
            return "IN"
        if vu == "VAR_FOUT":
            return "OUT"
        return None


def load_topology(vdt_path: Path | str) -> Topology:
    """
    Load a VEDA/TIMES .vdt topology file.

    Expected data rows (after comment/header lines):
        "Region","Process","Commodity","Direction"
    where Direction is IN or OUT.
    """
    vdt_path = Path(vdt_path)
    if not vdt_path.exists():
        raise FileNotFoundError(f"Topology file not found: {vdt_path}")

    rows: list[dict[str, str]] = []
    with vdt_path.open(encoding="utf-8", errors="replace") as f:
        for line in f:
            stripped = line.strip()
            if not stripped or stripped.startswith("*") or "=" in stripped[:20]:
                # Skip comments and ScenDesc= / ScenEDesc= header lines
                continue
            if not stripped.startswith('"'):
                continue
            parts = _parse_vdt_line(stripped)
            if len(parts) < 4:
                continue
            region, process, commodity, direction = (
                parts[0].strip(),
                parts[1].strip(),
                parts[2].strip(),
                parts[3].strip().upper(),
            )
            if direction not in {"IN", "OUT"}:
                continue
            rows.append(
                {
                    "region": region,
                    "process": process,
                    "commodity": commodity,
                    "direction": direction,
                }
            )

    links = pd.DataFrame(rows, columns=["region", "process", "commodity", "direction"])
    logger.info(
        "Loaded topology from %s: %d links, %d processes, %d commodities",
        vdt_path.name,
        len(links),
        links["process"].nunique() if not links.empty else 0,
        links["commodity"].nunique() if not links.empty else 0,
    )
    return Topology(links=links, source_path=vdt_path)


def flow_in_topology(
    topology: Topology,
    region: str,
    process: str,
    commodity: str,
    variable: str,
) -> bool:
    """Return True if the flow is declared in the topology for the variable direction."""
    direction = topology.direction_for_variable(variable)
    if direction is None:
        return False
    key = (str(region), str(process), str(commodity), direction)
    return key in topology.as_key_set()
