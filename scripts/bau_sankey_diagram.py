import os
import pandas as pd
import plotly.graph_objects as go
from collections import defaultdict

def get_energy_commodities(commodities_df):
    """
    Return a list of commodity codes that are likely energy carriers based on
    their code/description. Avoid pollutants and material flows.
    """
    energy_keywords = [
        'electricity', 'elc', 'coal', 'gas', 'oil', 'diesel', 'gasoline', 'petrol',
        'lpg', 'hfo', 'kerosene', 'biofuel', 'biomass', 'pellet', 'heat', 'steam',
        'solar', 'wind', 'hydro', 'nuclear', 'fuel', 'hydrogen'
    ]

    pollutant_or_material_keywords = [
        'co2', 'ch4', 'n2o', 'ghg', 'nox', 'sox', 'pm', 'pm2', 'cov', 'nh3',
        'ash', 'slag', 'waste', 'residue', 'sludge', 'dust', 'metal', 'ore'
    ]

    energy_commodities = set()

    for _, row in commodities_df.iterrows():
        commodity_code = str(row['Commodity'])
        description_text = str(row['Description'])
        comm = commodity_code.lower()
        desc = description_text.lower()

        contains_energy = any(keyword in comm or keyword in desc for keyword in energy_keywords)
        contains_pollutant_or_material = any(keyword in comm or keyword in desc for keyword in pollutant_or_material_keywords)

        if contains_energy and not contains_pollutant_or_material:
            energy_commodities.add(commodity_code)

    return list(energy_commodities)

def parse_times_line(line):
    """
    Parses a single line from the TIMES .vd file.
    """
    parts = []
    current_part = ""
    in_quotes = False
    
    for char in line.strip():
        if char == '"':
            in_quotes = not in_quotes
        elif char == ',' and not in_quotes:
            parts.append(current_part.strip('"'))
            current_part = ""
        else:
            current_part += char
    
    parts.append(current_part.strip('"'))
    return parts

def load_raw_records(vd_file_path, start_year=2021):
    """
    Stream and collect raw records with timeslice from the TIMES .vd file
    without filtering by variable type or commodity. Filtering can be
    applied later on the returned DataFrame.

    Returns a DataFrame with columns:
    [year, region, timeslice, variable, commodity_code, process_code, value]
    """
    flows = []
    print(f"Processing {vd_file_path} (all variables, all commodities)...")

    # --- Debugging counters ---
    line_count = 0
    kept_record_count = 0

    with open(vd_file_path, 'r') as f:
        for line in f:
            line_count += 1
            if not line.strip() or line.startswith('*'):
                continue

            try:
                parts = parse_times_line(line)
                if len(parts) < 9:
                    continue

                variable = parts[0]
                year = int(parts[3])
                if year < start_year:
                    continue

                value = float(parts[8])
                if value == 0:
                    continue

                commodity = parts[1]
                process = parts[2]
                region = parts[4]
                timeslice = parts[6]

                kept_record_count += 1
                flows.append({
                    'year': year,
                    'region': region,
                    'timeslice': timeslice,
                    'variable': variable,
                    'commodity_code': commodity,
                    'process_code': process,
                    'value': value
                })

            except (ValueError, IndexError):
                continue

    print("\n--- Processing Debug Info ---")
    print(f"Total lines scanned: {line_count}")
    print(f"Total records kept (year >= {start_year} and non-zero): {kept_record_count}")
    print("---------------------------\n")

    return pd.DataFrame(flows)


def aggregate_to_annual(flows_df):
    """
    Aggregate timeslices by summing to obtain annual values per
    [year, region, variable, commodity_code, process_code].
    """
    if flows_df.empty:
        return flows_df

    annual = (
        flows_df
        .groupby(['year', 'region', 'variable', 'commodity_code', 'process_code'], as_index=False)['value']
        .sum()
    )
    return annual


def infer_flow_direction(process_name, commodities_df):
    """
    Infers the source and target of a flow based on the process name.
    This is a heuristic and might need refinement.
    """
    process_upper = process_name.upper()

    # Heuristic 1: Imports
    if process_upper.startswith('IMP'):
        comm = process_upper[3:]
        # Find a matching commodity
        for c in commodities_df['Commodity']:
            if c.upper() in comm:
                return f"{c} Imports", c
        return "Imports", process_name

    # Heuristic 2: Exports
    if process_upper.startswith('EXP'):
        comm = process_upper[3:]
        for c in commodities_df['Commodity']:
            if c.upper() in comm:
                return c, f"{c} Exports"
        return process_name, "Exports"

    # Heuristic 3: Transportation
    if process_upper.startswith('TRA'):
        # This is tricky, as it can be a fuel consumption or a mode of transport.
        # Let's assume for now it's a demand sector.
        return "Transport Sector", f"Demand_{process_name}"

    # Heuristic 4: Electricity Generation
    if 'ELC' in process_upper:
        # Try to find input fuel
        for c in ['COA', 'GAS', 'OIL', 'NUC', 'BIO', 'HYD', 'WIN', 'SOL']:
             if c in process_upper:
                return c, "Electricity"
        return "Power Generation", "Electricity"

    # Default case
    return "Unknown Source", process_name

def filter_for_sankey(annual_df, commodities_df, year):
    """
    Filter annual aggregated flows for a given year, keeping only VAR_FIn and
    VAR_FOut variables and restricting to energy commodities.
    Returns the filtered DataFrame and the set of energy commodity codes.
    """
    if annual_df.empty:
        return annual_df, set()

    energy_codes = set(get_energy_commodities(commodities_df))

    df = annual_df.copy()
    df = df[df['year'] == year]

    var_upper = df['variable'].str.upper()
    df = df[var_upper.isin(['VAR_FIN', 'VAR_FOUT'])]

    if 'commodity_code' in df.columns:
        df = df[df['commodity_code'].isin(energy_codes)]

    return df, energy_codes


def analyze_process_connectivity(df):
    """
    Inspect which processes have both inflows (VAR_FIn) and outflows (VAR_FOut).
    Print warnings for isolated processes that have only inflows or only outflows.
    Returns a tuple of (both_io, only_in, only_out) as sets of process codes.
    """
    if df.empty:
        print("Filtered data is empty; cannot analyze connectivity.")
        return set(), set(), set()

    var_by_process = df.groupby('process_code')['variable'].apply(lambda s: set(v.upper() for v in s)).to_dict()
    both_io = set()
    only_in = set()
    only_out = set()

    for proc, vars_set in var_by_process.items():
        has_in = 'VAR_FIN' in vars_set
        has_out = 'VAR_FOUT' in vars_set
        if has_in and has_out:
            both_io.add(proc)
        elif has_in and not has_out:
            only_in.add(proc)
        elif has_out and not has_in:
            only_out.add(proc)

    print("\n--- Process Connectivity ---")
    print(f"Processes with both inflows and outflows: {len(both_io)}")
    if only_in:
        print(f"Warning: Processes with inflows only (potentially isolated): {len(only_in)}")
    if only_out:
        print(f"Warning: Processes with outflows only (potentially isolated): {len(only_out)}")
    print("--------------------------------\n")

    return both_io, only_in, only_out


def build_sankey(df, output_html_file, flow_threshold=0.0):
    """
    Build and save a Sankey diagram from filtered annual flows.
    Uses variable type to set direction:
    - VAR_FIn: commodity -> process
    - VAR_FOut: process -> commodity
    """
    if df.empty:
        print("No data to plot after filtering.")
        return None

    df = df.copy()
    var_upper = df['variable'].str.upper()
    df['source'] = df.apply(lambda r: r['commodity_code'] if r['variable'].upper() == 'VAR_FIN' else r['process_code'], axis=1)
    df['target'] = df.apply(lambda r: r['process_code'] if r['variable'].upper() == 'VAR_FIN' else r['commodity_code'], axis=1)

    # Aggregate duplicate links
    links_df = df.groupby(['source', 'target'], as_index=False)['value'].sum()

    if flow_threshold is not None and flow_threshold > 0:
        links_df = links_df[links_df['value'] > flow_threshold]

    if links_df.empty:
        print("No links above the threshold to plot.")
        return None

    nodes = pd.concat([links_df['source'], links_df['target']]).unique().tolist()
    node_index = {n: i for i, n in enumerate(nodes)}

    # Prepare labels using descriptions from the filtered data
    commodity_desc = df[['commodity_code', 'commodity']].dropna().drop_duplicates().set_index('commodity_code')['commodity'].to_dict()
    process_desc = df[['process_code', 'process']].dropna().drop_duplicates().set_index('process_code')['process'].to_dict()

    labels = []
    for n in nodes:
        if n in commodity_desc:
            labels.append(commodity_desc[n])
        elif n in process_desc:
            labels.append(process_desc[n])
        else:
            labels.append(n)

    sankey_links = {
        'source': links_df['source'].map(node_index).tolist(),
        'target': links_df['target'].map(node_index).tolist(),
        'value': links_df['value'].tolist(),
    }

    fig = go.Figure(data=[go.Sankey(
        node=dict(
            pad=15,
            thickness=20,
            line=dict(color="black", width=0.5),
            label=labels,
        ),
        link=sankey_links
    )])

    fig.update_layout(
        title_text="Energy Flow Diagram - 2021 (PJ)",
        font_size=10
    )
    fig.write_html(output_html_file)
    print(f"Saved Sankey diagram to {output_html_file}")
    return fig

def parse_metadata_file(file_path):
    """
    Parses a two-column CSV file with potential extra commas in the second column.
    """
    data = {}
    with open(file_path, 'r') as f:
        header = f.readline() # Skip header
        for line in f:
            line = line.strip()
            if not line:
                continue
            parts = line.split(',', 1)
            if len(parts) == 2:
                key, value = parts
                data[key.strip()] = value.strip()
    return data

def main():
    """Main function to generate the Sankey diagram."""
    # --- Configuration ---
    vd_file = "data/bau_080925_0809.vd"
    commodities_file = "data/commodities.csv"
    processes_file = "data/processes.csv"
    output_csv_file = "output/aggregated_flows_annual.csv"
    output_filtered_csv = "output/aggregated_flows_2021_energy_VARF.csv"
    output_html_file = "output/bau_sankey_2021_pj.html"
    start_year = 2021

    os.makedirs("output", exist_ok=True)

    # --- Load metadata ---
    print("Loading metadata...")
    commodities_map = parse_metadata_file(commodities_file)
    processes_map = parse_metadata_file(processes_file)
    commodities_df = pd.DataFrame(list(commodities_map.items()), columns=['Commodity', 'Description'])
    processes_df = pd.DataFrame(list(processes_map.items()), columns=['Process', 'Description'])

    # --- Load raw records (no filtering by variable or commodity) ---
    raw_flows_df = load_raw_records(vd_file, start_year=start_year)

    if raw_flows_df.empty:
        print("No valid energy flow data was processed. Exiting.")
        return

    # --- Aggregate to annual ---
    annual_flows_df = aggregate_to_annual(raw_flows_df)

    # --- Join descriptions ---
    annual_flows_df = (
        annual_flows_df
        .merge(commodities_df.rename(columns={"Commodity": "commodity_code", "Description": "commodity"}), on="commodity_code", how="left")
        .merge(processes_df.rename(columns={"Process": "process_code", "Description": "process"}), on="process_code", how="left")
    )

    # Reorder columns for readability
    ordered_cols = [
        'year', 'region', 'variable', 'commodity_code', 'commodity', 'process_code', 'process', 'value'
    ]
    for col in ordered_cols:
        if col not in annual_flows_df.columns:
            annual_flows_df[col] = None
    annual_flows_df = annual_flows_df[ordered_cols]

    # --- Save CSV ---
    print(f"Writing annual aggregated flows to {output_csv_file} ...")
    annual_flows_df.to_csv(output_csv_file, index=False)
    print("Done.")

    # --- Filter for Sankey (year=2021, VAR_F*, energy commodities) ---
    filtered_df, energy_codes = filter_for_sankey(annual_flows_df, commodities_df, year=2021)
    if filtered_df.empty:
        print("Filtered dataset for Sankey is empty; skipping Sankey generation.")
        return

    print(f"Writing filtered flows to {output_filtered_csv} ...")
    filtered_df.to_csv(output_filtered_csv, index=False)
    print("Done.")

    # --- Analyze connectivity and warn isolated processes ---
    both_io, only_in, only_out = analyze_process_connectivity(filtered_df)
    if only_in:
        print(f"Warning: {len(only_in)} processes have inflows only. Examples: {list(sorted(only_in))[:10]}")
    if only_out:
        print(f"Warning: {len(only_out)} processes have outflows only. Examples: {list(sorted(only_out))[:10]}")

    # --- Build Sankey ---
    _ = build_sankey(filtered_df, output_html_file, flow_threshold=0.0)

if __name__ == "__main__":
    main()
