import os
import pandas as pd

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

def get_all_commodities_from_vd(vd_file_path):
    """
    Scans the .vd file and returns a set of all unique commodity codes.
    """
    commodities = set()
    print(f"Scanning {vd_file_path} for unique commodities...")
    line_count = 0
    with open(vd_file_path, 'r') as f:
        for line in f:
            line_count += 1
            if not line.strip() or line.startswith('*'):
                continue
            try:
                parts = parse_times_line(line)
                if len(parts) > 1 and parts[0].strip().upper() in ('VAR_FIN', 'VAR_FOUT'):
                    commodities.add(parts[1])
            except (ValueError, IndexError):
                continue
    print(f"Found {len(commodities)} unique commodity codes in {line_count} lines.")
    return commodities

def read_commodity_mapping_table(mapping_file):
    """
    Read mapping table from CSV and normalize column names.
    """
    if not os.path.exists(mapping_file):
        # Return a DataFrame with the expected structure
        cols = [
            'TIMES commodity', 'Description', 'Unit', 'Sector', 'Type',
            'Cluster', 'PyPSA Energy Carrier', 'Upstream commodity',
            'Upstream process', 'Comment'
        ]
        return pd.DataFrame(columns=cols)

    df = pd.read_csv(mapping_file, engine='python')
    # Normalize columns for internal use
    cols_map = {}
    for c in df.columns:
        key = c.strip().lower().replace('\ufeff', '')
        cols_map[c] = key
    df = df.rename(columns=cols_map)

    def get(col_candidates):
        for c in col_candidates:
            if c in df.columns:
                return c
        return None

    pypsa_col = get(['cluster', 'pypsa energy carrier'])
    times_col = get(['times commodity', 'commodities times', 'times'])
    desc_col = get(['description'])
    unit_col = get(['unit'])
    sector_col = get(['sector (com_in)', 'sector'])
    type_col = get(['type'])
    upst_comm_col = get(['upstream commodity', 'uspstream_commodity'])
    upst_proc_col = get(['upstream process'])
    comment_col = get(['comment'])
    
    n = len(df)
    def series_or_empty(col_name):
        if col_name is not None and col_name in df.columns:
            return df[col_name]
        return pd.Series([''] * n)

    out = pd.DataFrame({
        'times': series_or_empty(times_col),
        'description': series_or_empty(desc_col),
        'unit': series_or_empty(unit_col),
        'sector': series_or_empty(sector_col),
        'type': series_or_empty(type_col),
        'pypsa': series_or_empty(pypsa_col),
        'upstream_commodity': series_or_empty(upst_comm_col),
        'upstream_process': series_or_empty(upst_proc_col),
        'comment': series_or_empty(comment_col),
    })
    # Strip whitespace
    for c in out.columns:
        out[c] = out[c].astype(str).map(lambda x: x.strip())
    # Drop empty times codes
    out = out[out['times'] != '']
    return out

def write_commodity_mapping_table(mapping_file, mapping_df):
    """Writes the commodity mapping DataFrame to a CSV file."""
    cols = [
        'TIMES commodity', 'Description', 'Unit', 'Sector', 'Type',
        'Cluster', 'PyPSA Energy Carrier', 'Upstream commodity',
        'Upstream process', 'Comment'
    ]

    output_df = pd.DataFrame(columns=cols)
    for col in cols:
        map_col_name = {
            'TIMES commodity': 'times',
            'Description': 'description',
            'Unit': 'unit',
            'Sector': 'sector',
            'Type': 'type',
            'Cluster': 'pypsa',
            'PyPSA Energy Carrier': 'pypsa',
            'Upstream commodity': 'upstream_commodity',
            'Upstream process': 'upstream_process',
            'Comment': 'comment'
        }.get(col, None)

        if map_col_name and map_col_name in mapping_df.columns:
            output_df[col] = mapping_df[map_col_name]
        else:
            output_df[col] = ''

    output_df = output_df.loc[:, ~output_df.columns.duplicated()]
    if 'Cluster' in output_df.columns:
        output_df['PyPSA Energy Carrier'] = output_df['Cluster']

    output_df.to_csv(mapping_file, index=False)


def main():
    """
    Main function to update the commodity mapping file.
    """
    vd_file = "data/bau_080925_0809.vd"
    mapping_file = "data/mapping_commodities.csv"
    all_commodities_file = "data/AllCommodities.csv"

    print("--- Starting Commodity Mapping Update ---")
    
    # 1. Get all unique commodities from the .vd file
    used_commodities = get_all_commodities_from_vd(vd_file)

    # 2. Load existing mapping
    mapping_df = read_commodity_mapping_table(mapping_file)
    mapped_commodities = set(mapping_df['times'].unique())

    # 3. Find missing commodities
    missing_commodities = used_commodities - mapped_commodities
    print(f"Found {len(missing_commodities)} commodities in use but not in mapping file.")

    if not missing_commodities:
        print("Commodity mapping is up to date. No changes needed.")
        return

    # 4. Load AllCommodities.csv to get metadata
    if not os.path.exists(all_commodities_file):
        print(f"Error: {all_commodities_file} not found. Cannot add missing commodities.")
        return
    
    all_commodities_df = pd.read_csv(all_commodities_file, sep=';', header=None)
    if len(all_commodities_df.columns) > 7:
        all_commodities_df = all_commodities_df.rename(columns={
            4: 'Commodity',
            5: 'Description',
            6: 'Unit',
            3: 'Sector', # .NRG.
            7: 'Type' # NRG
        })
    else:
        print("Warning: AllCommodities.csv has an unexpected number of columns.")
        return

    # 5. Filter missing commodities and prepare new rows
    new_rows = []
    for code in sorted(list(missing_commodities)):
        info = all_commodities_df.loc[all_commodities_df['Commodity'] == code]
        if not info.empty:
            unit = info.iloc[0]['Unit']
            if str(unit).strip().upper() == 'PJ':
                desc = info.iloc[0]['Description']
                print(f"[INFO] Adding '{code}' ({desc}) with Unit=PJ to mapping.")
                new_rows.append({
                    'pypsa': desc,  # Use description as the cluster name by default
                    'times': code,
                    'description': desc,
                    'unit': unit,
                    'sector': info.iloc[0]['Sector'],
                    'type': info.iloc[0]['Type'],
                    'upstream_commodity': '',
                    'upstream_process': '',
                    'comment': 'auto-added by script'
                })
            else:
                 print(f"[INFO] Skipping '{code}' with Unit='{unit}' (not PJ).")
        else:
            print(f"[WARN] Commodity '{code}' not found in {all_commodities_file}. Cannot add.")
    
    # 6. Add new rows and write back to file
    if new_rows:
        updated_mapping_df = pd.concat([mapping_df, pd.DataFrame(new_rows)], ignore_index=True)
        updated_mapping_df = updated_mapping_df.sort_values(by='times').reset_index(drop=True)
        write_commodity_mapping_table(mapping_file, updated_mapping_df)
        print(f"\nSuccessfully added {len(new_rows)} new commodities to {mapping_file}.")
    else:
        print("\nNo new commodities with Unit=PJ found to add.")
    
    print("--- Commodity Mapping Update Finished ---")

if __name__ == "__main__":
    main()
