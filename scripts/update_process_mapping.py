import pandas as pd
import os

def update_process_mapping():
    """
    Loads the mapping_processes.csv file, adds a new 'Aggregation Level 1'
    column, and saves the updated file.
    """
    # Define file paths
    base_path = os.path.dirname(__file__)
    data_path = os.path.abspath(os.path.join(base_path, '..', 'data'))
    
    mapping_file = os.path.join(data_path, 'mapping_processes.csv')
    tech_file = os.path.join(data_path, 'techs', 'tech_times-wal_unique.csv')

    # 1. Load the mapping CSV file
    df_map = pd.read_csv(mapping_file)

    # 2. Create the base 'Aggregation Level 1' column
    df_map['Aggregation Level 1'] = (
        df_map['Sector'].fillna('').astype(str) + '_' +
        df_map['Type'].fillna('').astype(str) + '_' +
        df_map['Activity unit'].fillna('').astype(str) + '_' +
        df_map['Capacity unit'].fillna('').astype(str)
    )

    # 3. Load the tech file and filter for relevant CHP entries
    df_tech = pd.read_csv(tech_file)
    df_tech_chp = df_tech[df_tech['CHP'].notna() & (df_tech['CHP'] != 'FALSE')].copy()
    df_tech_chp = df_tech_chp[['process', 'CHP']]
    df_tech_chp.rename(columns={'process': 'Technology (Process)'}, inplace=True)

    # 4. Merge to add CHP information
    df_map = pd.merge(df_map, df_tech_chp, on='Technology (Process)', how='left')

    # 5. Append CHP value to 'Aggregation Level 1' where it exists
    # Make sure CHP column is string type for concatenation
    df_map['CHP'] = df_map['CHP'].astype(str)
    chp_not_nan_mask = df_map['CHP'].notna() & (df_map['CHP'] != 'nan')
    df_map.loc[chp_not_nan_mask, 'Aggregation Level 1'] = \
        df_map.loc[chp_not_nan_mask, 'Aggregation Level 1'] + \
        ' (' + df_map.loc[chp_not_nan_mask, 'CHP'] + ')'

    # 6. Drop the temporary CHP column
    df_map.drop(columns=['CHP'], inplace=True)

    # 7. Save the updated dataframe back to the original file
    df_map.to_csv(mapping_file, index=False)

    print(f"Successfully updated {mapping_file} with 'Aggregation Level 1' column including CHP info.")
    print("First 5 rows of the updated file:")
    print(df_map[['Technology (Process)', 'Aggregation Level 1']].head())

if __name__ == "__main__":
    update_process_mapping()
