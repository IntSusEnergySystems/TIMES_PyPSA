#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Sep 19 11:10:31 2025
"""

import pandas as pd
selected_year=2050
def pypsa_demands(selected_year, mapping_file="../data/pypsa_mapping.csv"):
    df = pd.read_csv(f"output/annual_values_{selected_year}.csv")

    grouped_data = df.groupby(['commodity', 'clustered_process']).agg(
        total_value=('value', 'sum'),
        record_count=('value', 'count')
    )
    # Convert PJ → TWh
    grouped_data['total_value'] = grouped_data['total_value'] / 3.6
    efficiency = 0.8
    # subtract navigation from transport diesel
    try:
        diesel_val = grouped_data.loc[("total transport", "Fuel Tech - Diesel"), "total_value"]
        nav_val = grouped_data.loc[("total navigation", "Navigation Domestic Freight Tech Existing"), "total_value"]
        grouped_data.loc[("total transport", "Fuel Tech - Diesel"), "total_value"] = diesel_val - nav_val
        #Converting high temerature heat demand in gas demand considering a system efficiency of 80%
        heat_val = grouped_data.loc[("Fuel Tech - Heat", "Fuel Tech - Heat"), "total_value"]
        adjusted_heat_val = heat_val / efficiency
        grouped_data.loc[("Fuel Tech - Heat", "Fuel Tech - Heat"), "total_value"] = adjusted_heat_val
    except KeyError as e:
        print(f"Adjustment skipped: {e}")

    mapping_df = pd.read_csv(mapping_file)

    new_data = []
    for category, subset in mapping_df.groupby("category"):
        summed_value = 0
        for _, row in subset.iterrows():
            key = (row["commodity"], row["clustered_process"])
            try:
                value = grouped_data.loc[key, "total_value"]
                summed_value += value
            except KeyError:
                print(f"Key not found in grouped_data: {key} for category '{category}'")

        new_data.append({"category": category, "TWh": summed_value})

    pypsa_values = pd.DataFrame(new_data)
    output_file = f"output/pypsa_demands_{selected_year}.csv"
    pypsa_values.to_csv(output_file, index=False)

    print(f"Saved: {output_file}")
    return pypsa_values
pypsa_demands(selected_year)