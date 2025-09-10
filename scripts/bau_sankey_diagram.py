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

def load_raw_flows(vd_file_path, energy_commodities_set, start_year=2021):
    """
    Stream and collect raw VAR_Fin/VAR_Fout records with timeslice from the
    TIMES .vd file.

    Returns a DataFrame with columns:
    [year, region, timeslice, variable, commodity_code, process_code, value]
    """
    flows = []
    print(f"Processing {vd_file_path} using VAR_Fin and VAR_Fout...")

    # --- Debugging counters ---
    line_count = 0
    flow_line_count = 0
    dash_commodity_count = 0
    energy_commodity_count = 0
    other_commodity_count = 0
    other_examples = []

    with open(vd_file_path, 'r') as f:
        for line in f:
            line_count += 1
            if not line.strip() or line.startswith('*'):
                continue

            try:
                parts = parse_times_line(line)
                if len(parts) < 9:
                    continue

                variable_raw = parts[0]
                variable_upper = variable_raw.upper()
                if not variable_upper.startswith("VAR_F"):
                    continue

                # Normalize variable name casing to one of {VAR_Fin, VAR_Fout}
                variable = 'VAR_Fin' if 'IN' in variable_upper else ('VAR_Fout' if 'OUT' in variable_upper else None)
                if variable is None:
                    continue

                flow_line_count += 1
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

                if commodity == '-':
                    dash_commodity_count += 1
                    continue

                if commodity in energy_commodities_set:
                    energy_commodity_count += 1
                    flows.append({
                        'year': year,
                        'region': region,
                        'timeslice': timeslice,
                        'variable': variable,
                        'commodity_code': commodity,
                        'process_code': process,
                        'value': value
                    })
                else:
                    other_commodity_count += 1
                    if len(other_examples) < 10:
                        other_examples.append(commodity)

            except (ValueError, IndexError):
                continue

    print("\n--- Processing Debug Info ---")
    print(f"Total lines scanned: {line_count}")
    print(f"Total VAR_F* lines found: {flow_line_count}")
    print(f"Flows with '-' as commodity (skipped): {dash_commodity_count}")
    print(f"Flows with a non-energy commodity (skipped): {other_commodity_count}")
    if other_examples:
        print(f"Examples of non-energy commodities found: {list(set(other_examples))}")
    print(f"Flows with a valid energy commodity (processed): {energy_commodity_count}")
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

def create_sankey_diagram(*args, **kwargs):
    """Placeholder retained for future use. Sankey generation is paused for now."""
    return None

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
    start_year = 2021

    os.makedirs("output", exist_ok=True)

    # --- Load metadata ---
    print("Loading metadata...")
    commodities_map = parse_metadata_file(commodities_file)
    processes_map = parse_metadata_file(processes_file)
    commodities_df = pd.DataFrame(list(commodities_map.items()), columns=['Commodity', 'Description'])
    processes_df = pd.DataFrame(list(processes_map.items()), columns=['Process', 'Description'])

    # Build energy commodity filter
    energy_commodities = get_energy_commodities(commodities_df)
    energy_commodities_set = set(energy_commodities)
    print(f"Found {len(energy_commodities)} energy commodities.")

    # --- Load raw flows ---
    raw_flows_df = load_raw_flows(vd_file, energy_commodities_set, start_year=start_year)

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

if __name__ == "__main__":
    main()
