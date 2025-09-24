### TIMES_PyPSA
Soft-linking between the TIMES‑WAL and PyPSA‑WAL models. This repository includes a parser/visualizer that reads TIMES `.vd` output files and generates interactive Sankey energy-flow diagrams and a power capacity bar chart.

### Requirements
- **Python**: 3.9+ recommended
- **Libraries**: `pandas`, `plotly` (and `jupyter` if using the notebook)

Install with pip:
```bash
python -m pip install --upgrade pip
pip install pandas plotly jupyter
```

### Data
Files under `data/`:
- bau_080925_0809.vd: example TIMES output file parsed by the script
- mapping_commodities.csv, mapping_processes.csv: mapping the TIMES processes and commodities towards the PyPSA equivalents

### Example outputs
Generated files (synced to the web server by the rsync script):
- [bau_sankey_2021_pj_clustered.html](http://labothap.squoilin.eu/times_pypsa/bau_sankey_2021_pj_clustered.html)
- [bau_sankey_2050_pj.html](http://labothap.squoilin.eu/times_pypsa/bau_sankey_2050_pj.html)
- [annual_values_2021.csv](http://labothap.squoilin.eu/times_pypsa/annual_values_2021.csv)
- [annual_values_2050.csv](http://labothap.squoilin.eu/times_pypsa/annual_values_2050.csv)
- [annual_values.csv](http://labothap.squoilin.eu/times_pypsa/annual_values.csv)
- [annual_values_clustered.csv](http://labothap.squoilin.eu/times_pypsa/annual_values_clustered.csv)
- [annual_flows_2021_energy_clustered.csv](http://labothap.squoilin.eu/times_pypsa/annual_flows_2021_energy_clustered.csv)
- [annual_flows_2050_energy.csv](http://labothap.squoilin.eu/times_pypsa/annual_flows_2050_energy.csv)
- [sankey_commodity_groups_2021.csv](http://labothap.squoilin.eu/times_pypsa/sankey_commodity_groups_2021.csv)
- [sankey_commodity_groups_2021.json](http://labothap.squoilin.eu/times_pypsa/sankey_commodity_groups_2021.json)

### How to run
Run the Python script (recommended):
```bash
cd scripts
python bau_sankey_diagram.py
```
This reads data/bau_080925_0809.vd, creates interactive Sankey diagrams in PJ, a capacity bar plot, and exports CSVs to `output/`.

Run the notebook (currently running with a simplistic demo output file):
```bash
jupyter lab  # or: jupyter notebook
```
Then open `scripts/run_sankey.ipynb` and run all cells.
