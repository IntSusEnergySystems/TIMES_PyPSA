#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Created on Fri Sep 19 11:10:31 2025
"""

import pandas as pd
selected_year = 2050
def pypsa_demands(selected_year):
 df=pd.read_csv(f"output/annual_values_{selected_year}.csv")

 grouped_data = df.groupby(['commodity', 'clustered_process']).agg(
    total_value=('value', 'sum'),
    record_count=('value', 'count')
)
 #Convert PJ to TWh
 grouped_data['total_value'] = grouped_data['total_value'] / 3.6
 #subtract navigation from transport diesel
 try:
    diesel_val = grouped_data.loc[("total transport", "Fuel Tech - Diesel"), "total_value"]
    nav_val = grouped_data.loc[("total navigation", "Navigation Domestic Freight Tech Existing"), "total_value"]

    grouped_data.loc[("total transport", "Fuel Tech - Diesel"), "total_value"] = diesel_val - nav_val
 except KeyError as e:
    print(f"Adjustment skipped: {e}")
 pypsa_mapping = {
    'total residential space': [
        ('Coal for residential sector', 'space heating residential'),
        ('Diesel Oil for the residential sector', 'space heating residential'),
        ('Electricity for Residential sector', 'space heating residential'),
        ('Liquified Petroleum Gas (RSD)', 'space heating residential'),
        ('Network gas for Residential sector', 'space heating residential'),
        ('Pellets for Residential sector', 'space heating residential'),
        ('Wood Logs for Residential sector', 'space heating residential'),
        ('Heat for Residential sector', 'space heating residential'),
    ],
    'electricity residential space': [
        ('Electricity for Residential sector', 'space heating residential'),
    ],
    'total residential water': [
        ('Coal for residential sector', 'residential hot water'),
        ('Diesel Oil for the residential sector', 'residential hot water'),
        ('Electricity for Residential sector', 'residential hot water'),
        ('Liquified Petroleum Gas (RSD)', 'residential hot water'),
        ('Network gas for Residential sector', 'residential hot water'),
        ('Pellets for Residential sector', 'residential hot water'),
        ('Wood Logs for Residential sector', 'residential hot water'),
        ('Residential: Solar', 'residential hot water'),
    ],
    'electricity residential water': [
       ('Electricity for Residential sector', 'residential hot water'),
    ],
    'total residential': [
        ('Coal for residential sector', 'space heating residential'),
        ('Diesel Oil for the residential sector', 'space heating residential'),
        ('Electricity for Residential sector', 'space heating residential'),
        ('Liquified Petroleum Gas (RSD)', 'space heating residential'),
        ('Network gas for Residential sector', 'space heating residential'),
        ('Pellets for Residential sector', 'space heating residential'),
        ('Wood Logs for Residential sector', 'space heating residential'),
        ('Heat for Residential sector', 'space heating residential'),
        ('Coal for residential sector', 'residential hot water'),
        ('Diesel Oil for the residential sector', 'residential hot water'),
        ('Electricity for Residential sector', 'residential hot water'),
        ('Liquified Petroleum Gas (RSD)', 'residential hot water'),
        ('Network gas for Residential sector', 'residential hot water'),
        ('Pellets for Residential sector', 'residential hot water'),
        ('Wood Logs for Residential sector', 'residential hot water'),
        ('Residential: Solar', 'residential hot water'),
        ('Coal for residential sector', 'residential gas appliances'),
        ('Electricity for Residential sector', 'PV residential'),
        ('Electricity for Residential sector', 'residential electrical appliances'),
        ('Gasoline for Residential sector', 'Other residential'),
        ('Liquified Petroleum Gas (RSD)', 'residential gas appliances'),
        ('Network gas for Residential sector', 'residential gas appliances'),
        ('Wood Logs for Residential sector', 'residential gas appliances'),
    ],
    'electricity residential': [
        ('Electricity for Residential sector', 'space heating residential'),
        ('Electricity for Residential sector', 'residential hot water'),
        ('Residential: Solar', 'residential hot water'),
        ('Electricity for Residential sector', 'PV residential'),
        ('Electricity for Residential sector', 'residential electrical appliances'),
    ],
    'total services space': [
        ('Biogas for Commercial sector', 'Space heating services'),
        ('Electricity for Commercial sector', 'Space heating services'),
        ('LT Heat for Commercial sector', 'Space heating services'),
        ('Liquified Petroleum Gas (COM)', 'Space heating services'),
        ('Network gas for Commercial sector', 'Space heating services'),
        ('Oil for commercial sector', 'Space heating services'),
        ('Pellets for Commercial sector', 'Space heating services'),
    ],
    'electricity services space': [
        ('Electricity for Commercial sector', 'Space heating services'),
    ],
    'total services water': [
        ('Biogas for Commercial sector', 'services hot water'),
        ('Commercial: Solar', 'services hot water'),
        ('Electricity for Commercial sector', 'services hot water'),
        ('LT Heat for Commercial sector', 'services hot water'),
        ('Liquified Petroleum Gas (COM)', 'services hot water'),
        ('Network gas for Commercial sector', 'services hot water'),
        ('Oil for commercial sector', 'services hot water'),
        ('Pellets for Commercial sector', 'services hot water'),
    ],
    'electricity services water': [
        ('Commercial: Solar', 'services hot water'),
        ('Electricity for Commercial sector', 'services hot water'),
    ],
    'total services': [
        ('Biogas for Commercial sector', 'Space heating services'),
        ('Biodiesel for Commercial sector', 'tertiary electricity'),
        ('Electricity for Commercial sector', 'Space heating services'),
        ('LT Heat for Commercial sector', 'Space heating services'),
        ('Liquified Petroleum Gas (COM)', 'Space heating services'),
        ('Network gas for Commercial sector', 'Space heating services'),
        ('Oil for commercial sector', 'Space heating services'),
        ('Pellets for Commercial sector', 'Space heating services'),
        ('Biogas for Commercial sector', 'services hot water'),
        ('Commercial: Solar', 'services hot water'),
        ('Electricity for Commercial sector', 'services hot water'),
        ('LT Heat for Commercial sector', 'services hot water'),
        ('Liquified Petroleum Gas (COM)', 'services hot water'),
        ('Network gas for Commercial sector', 'services hot water'),
        ('Oil for commercial sector', 'services hot water'),
        ('Pellets for Commercial sector', 'services hot water'),
        ('Commercial: Solar', 'PV commercial'),
        ('Electricity for Commercial sector', 'PV commercial'),
        ('Gasoline for Commercial sector', 'tertiary electricity'),
        ('Liquified Petroleum Gas (COM)', 'tertiary electricity'),
        ('Liquified Petroleum Gas (COM)', 'total services cooking'),
        ('Network gas for Commercial sector', 'tertiary electricity'),
        ('Network gas for Commercial sector', 'other services'),
        ('Network gas for Commercial sector', 'tertiary CHP'),
        ('Network gas for Commercial sector', 'total services cooking'),
        ('Oil for commercial sector', 'tertiary electricity'),
        ('Pellets for Commercial sector', 'tertiary electricity'),
        ('Electricity for Commercial sector', 'tertiary electricity'),
        ('Electricity for Commercial sector', 'tertiary electricity cooking'),
    ],
    'electricity services': [
        ('Electricity for Commercial sector', 'Space heating services'),
        ('Commercial: Solar', 'services hot water'),
        ('Electricity for Commercial sector', 'services hot water'),
        ('Electricity for Commercial sector', 'PV commercial'),
        ('Electricity for Commercial sector', 'tertiary electricity'),
        ('Electricity for Commercial sector', 'tertiary electricity cooking'),
    ],
    'total agriculture heat': [
        ('total agriculture', 'heat for agriculture'),
    ],
    'total agriculture electricity': [
        ('total agriculture', 'agriculture electricity'),
    ],
    'total agriculture machinery oil': [
        ('total agriculture', 'oil for agriculture'),
    ],
    'total agriculture': [
        ('total agriculture', 'heat for agriculture'),
        ('total agriculture', 'agriculture electricity'),
        ('total agriculture', 'oil for agriculture'),
    ],
    'total road': [
        ('total transport', 'Fuel Tech - Diesel'),
        ('total transport', 'Fuel Tech - Gasoline'),
        ('total transport', 'Fuel Tech - Liquified Petroleum Gas'),
        ('total transport', 'Fuel Tech - Reseau gas mixed'),
        ('Electricity for Commercial sector', 'EV charger'),
        ('total transport', 'Fuel Tech - Biogas'),
        ('total transport', 'Fuel Tech - Ethanol'),
        ('total transport', 'Fuel Tech - H2'),
    ],
    'electricity road': [
       ('Electricity for Commercial sector', 'EV charger'),
    ],
    'hydrogen road': [
       ('total transport', 'Fuel Tech - H2'),
    ],
    'total rail': [
        ('total rail', 'electricity transport'),
    ],
    'total navigation': [
        ('total navigation', 'Navigation Domestic Freight Tech Existing'),
    ],
    'total aviation': [
        ('Kerosene - Jet Fuels for transport', 'Aviation'),
        ('Fuel Tech - H2', 'Aviation'),
    ],
    'industry electricity': [
        ('industry electricity', 'Fuel Tech - Electricity'),
        ('industry electricity', 'PV industrial'),
        ('industry electricity', 'PV'),
        ('industry electricity', 'industrial CHP'),
    ],
    'industry coal': [
        ('coal for industry', 'Fuel Tech - Hard Coal'),
        ('coal for industry', 'Fuel Tech - Lignite'),
    ],
    'industry coke': [
        ('coke for industry', 'Fuel Tech - Coke'),
    ],
    'industry solid biomass': [
        ('solid biomass for industry', 'Fuel Tech - Wood Chips'),
    ],
    'industry methane': [
        ('gas for industry', 'Fuel Tech - Biogas'),
        ('gas for industry', 'Fuel Tech - Liquified Petroleum Gas'),
        ('gas for industry', 'Fuel Tech - Natural Gas transport'),
        ('gas for industry', 'Fuel Tech - Natural Gas and biogas mixed'),
        ('gas for industry', 'Fuel Tech - Gas and Cog industry'),
        ('gas for industry', 'Fuel Tech New - IIS GMX - Corex GMX or BFG TGR with CCS or BOF'),
    ],
    'industry low temperature heat': [
        ('Fuel Tech - Heat', 'Fuel Tech 1 - Heat'),
    ],
    'industry high temperature heat': [
        ('Fuel Tech - Heat', 'Fuel Tech - Heat'),
        ('Fuel Tech - Heat', 'industrial CHP'),
    ],
    'industry naphtha': [
        ('Diesel', 'Non-energy'),
        ('Hard Coal', 'Non-energy'),
        ('Natural Gas', 'Non-energy'),
    ],
    'industry oil': [
        ('oil for industry', 'Fuel Tech - Heavy Fuel Oil'),
        ('oil for industry', 'Fuel Tech - Light Fuel Oil'),
    ],
    'industry hydrogen': [
        ('hydrogen for industry', 'Imports'),
    ],
}

 new_data = []
 for category, keys in pypsa_mapping.items():
    summed_value = 0
    for key in keys:
        try:
            value = grouped_data.loc[key, 'total_value']
            summed_value += value
        except KeyError:
            print(f"Key not found in grouped_data: {key} for category '{category}'")
    
    new_data.append({
        'category': category,
        'TWh': summed_value
    })

# Create a new DataFrame from the list of results
 pypsa_values = pd.DataFrame(new_data)
 output_file = f"output/pypsa_demands_{selected_year}.csv"
 pypsa_values.to_csv(output_file, index=False)

 print(f"Saved: {output_file}")
 return pypsa_values
pypsa_demands(selected_year)