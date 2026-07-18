### TIMES_PyPSA

Soft-linking between the TIMES-WAL and PyPSA-WAL models. This repository provides the **`times_pypsa`** Python package to parse TIMES `.vd` output files, extract PyPSA demand categories, and generate interactive Sankey energy-flow diagrams.

### Install

From the repository root:

```bash
python -m pip install --upgrade pip
pip install -e .
```

Dependencies: `pandas`, `plotly` (Python 3.9+).

Bundled mapping files live in `times_pypsa/mappings/` (`mapping_commodities.csv`, `mapping_processes.csv`, `extraction_rules.csv`).

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

Multi-view extraction QA (export highlighting, balance / coverage CSVs) — see [EXTRACTION_QA.md](EXTRACTION_QA.md):

```bash
times-pypsa qa \
  --vd /path/to/scen_corrige_251129_0112.vd \
  --vdt /path/to/scen_corrige_251129_0112.vdt \
  --year 2050 \
  --out-dir output/qa_2050/
```

Use `--mappings-dir` to override the bundled mappings (defaults to the package `times_pypsa/mappings/`).

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

### Python API

For Snakemake integration in pypsa-wal:

```python
from times_pypsa import export_horizon, export_all_horizons, generate_sankey

export_horizon(
    vd_file="path/to/scenario.vd",
    mappings_dir="times_pypsa/mappings",
    horizon=2030,
    wallon_demands_path="resources/walloon/demands_2030.csv",
    heating_capacities_path="resources/walloon/heating_capacities_2030.csv",
    sankey_dir="results/sankey",
    emit_sankey=True,
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
- `mapping_commodities.csv`, `mapping_processes.csv`: TIMES → PyPSA mappings (also shipped in the package)

### Example outputs

Generated files (synced to the web server by the rsync script):

- [bau_sankey_2021_pj_clustered.html](http://labothap.squoilin.eu/times_pypsa/bau_sankey_2021_pj_clustered.html)
- [bau_sankey_2050_pj.html](http://labothap.squoilin.eu/times_pypsa/bau_sankey_2050_pj.html)
- [annual_values_2021.csv](http://labothap.squoilin.eu/times_pypsa/annual_values_2021.csv)
- [annual_values_clustered.csv](http://labothap.squoilin.eu/times_pypsa/annual_values_clustered.csv)
