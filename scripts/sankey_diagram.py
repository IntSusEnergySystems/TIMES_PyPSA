#!/usr/bin/env python3
"""
TIMES Model Energy Flow Sankey Diagram Generator

This script processes TIMES model .vd output files and creates Plotly Sankey diagrams
to visualize ENERGY flows only (in PJ or TWh units).
"""

import pandas as pd
import plotly.graph_objects as go
from collections import defaultdict
from read_times_output import TIMESOutputReader


class TIMESEnergyFlowProcessor(TIMESOutputReader):
    def __init__(self, vd_file_path, cache_path='../output/data_cache.pkl'):
        super().__init__(vd_file_path, cache_path)
        self._en_pairs_cache = None
        
    def _convert_units(self, value_pj, target_unit):
        """Convert energy values from PJ to target unit"""
        if target_unit.upper() == 'TWH':
            return value_pj * 0.277778  # 1 PJ = 0.277778 TWh
        return value_pj  # Default to PJ
    
    def _get_unit_label(self, unit):
        """Get display label for energy units"""
        return 'TWh' if unit.upper() == 'TWH' else 'PJ'
    
    def create_interactive_sankey(self, years_to_plot=None, flow_threshold=10.0, unit='PJ', show=False):
        """Create interactive Sankey diagram with dropdown to select year"""
        if not years_to_plot:
            years_to_plot = [year for year in self.processed_data.keys() if year <= 2050]
        
        print(f"\nCreating interactive Sankey diagram for years: {years_to_plot} in {unit}")
        
        # Create Sankey diagrams for each year
        sankey_figures = {}
        for year in years_to_plot:
            if year in self.processed_data:
                # Create the data structure for this year without creating the full figure
                year_data = self.processed_data[year]
                energy_data = year_data[year_data['variable'] == 'VAR_Act'].copy()
                
                if not energy_data.empty:
                    # Aggregate flows by process (sum across time slices)
                    aggregated_flows = {}
                    
                    for _, row in energy_data.iterrows():
                        value = abs(row['value'])
                        if value < flow_threshold:
                            continue
                            
                        process_name = row['process']
                        source, target = self._get_energy_flow_direction(process_name)
                        
                        if source and target:
                            # Create a key for aggregation (process + source + target)
                            flow_key = (process_name, source, target)
                            
                            if flow_key in aggregated_flows:
                                # Sum the values for the same process across different time slices
                                aggregated_flows[flow_key]['value'] += value
                            else:
                                # Create new aggregated flow
                                aggregated_flows[flow_key] = {
                                    'source': source,
                                    'target': target,
                                    'value': value,
                                    'process': process_name
                                }
                    
                    # Convert aggregated flows to list
                    flows = list(aggregated_flows.values())
                    nodes_set = set()
                    
                    for flow in flows:
                        nodes_set.add(flow['source'])
                        nodes_set.add(flow['target'])
                    
                    if flows:
                        # Create node list and mapping
                        nodes = list(nodes_set)
                        node_dict = {node: i for i, node in enumerate(nodes)}
                        
                        # Convert flows to use node indices
                        source_indices = [node_dict[flow['source']] for flow in flows]
                        target_indices = [node_dict[flow['target']] for flow in flows]
                        values = [flow['value'] for flow in flows]
                        
                        # Print energy balance summary only for the last considered year
                        is_last_year = (year == years_to_plot[-1])
                        self._check_energy_balance(nodes, flows, year, unit, print_summary=is_last_year)
                        
                        # Store the Sankey data for this year
                        sankey_figures[year] = {
                            'nodes': nodes,
                            'source_indices': source_indices,
                            'target_indices': target_indices,
                            'values': values,
                            'flows': flows
                        }
        
        if not sankey_figures:
            print("No Sankey diagrams could be created for any year")
            return None
        
        # Get the first year's data as base
        first_year = list(sankey_figures.keys())[0]
        base_data = sankey_figures[first_year]
        
        # Create the interactive figure
        fig = go.Figure()
        
        # Add traces for each year
        for year, year_data in sankey_figures.items():
            # Convert node names to user-friendly labels with units
            friendly_labels = [self._get_user_friendly_label(node, unit) for node in year_data['nodes']]
            
            # Convert values to target unit
            converted_values = [self._convert_units(value, unit) for value in year_data['values']]
            
            # Create tooltips with values and units
            tooltips = []
            unit_label = self._get_unit_label(unit)
            for i, value in enumerate(year_data['values']):
                source_label = friendly_labels[year_data['source_indices'][i]]
                target_label = friendly_labels[year_data['target_indices'][i]]
                converted_value = self._convert_units(value, unit)
                tooltip = f"{source_label} → {target_label}<br>Value: {converted_value:.1f} {unit_label}"
                tooltips.append(tooltip)
            
            # Generate flow colors based on process type
            flow_colors = self._generate_flow_colors(year_data['flows'])
            
            # Create a trace for this year
            trace = go.Sankey(
                link=dict(
                    source=year_data['source_indices'],
                    target=year_data['target_indices'],
                    value=converted_values,
                    color=flow_colors,
                    hovertemplate='%{customdata}<extra></extra>',
                    customdata=tooltips
                ),
                node=dict(
                    label=friendly_labels,
                    color=self._generate_node_colors(year_data['nodes']),
                    hovertemplate=f'%{{label}}<br>Units: {unit_label}<extra></extra>'
                ),
                name=f"Year {year}",
                visible=(year == first_year)  # Only first year visible initially
            )
            fig.add_trace(trace)
        
        # Create dropdown buttons
        buttons = []
        for i, year in enumerate(sankey_figures.keys()):
            # Create visibility array for this year
            visible = [False] * len(sankey_figures)
            visible[i] = True
            
            buttons.append(
                dict(
                    label=f"Year {year}",
                    method="update",
                    args=[
                        {"visible": visible},
                        {"title": f"TIMES Energy Flow Diagram - {year} (Units: {self._get_unit_label(unit)})"}
                    ]
                )
            )
        
        # Add "Show All" button
        buttons.append(
            dict(
                label="Show All Years",
                method="update",
                args=[
                    {"visible": [True] * len(sankey_figures)},
                    {"title": f"TIMES Energy Flow Diagram - All Years (Units: {self._get_unit_label(unit)})"}
                ]
            )
        )
        
        # Update layout with dropdown moved to the right
        fig.update_layout(
            title=f"TIMES Energy Flow Diagram - {first_year} (Units: {self._get_unit_label(unit)})",
            font_size=12,
            width=1200,
            height=800,
            updatemenus=[
                dict(
                    buttons=buttons,
                    direction="down",
                    showactive=True,
                    x=0.9,  # Moved to the right
                    xanchor="right",  # Anchored to the right
                    y=1.02,
                    yanchor="top"
                )
            ]
        )
        
        print(f"Created interactive Sankey with {len(sankey_figures)} years")
        if show:
            try:
                fig.show()
            except Exception:
                pass
        return fig
    
    def _get_energy_flow_direction(self, process_name):
        """Get energy flow direction for a process (simplified energy-only logic)"""
        process = process_name.upper()
        
        # Primary energy extraction (mining) vs imports
        if process.startswith('MIN'):
            if 'COA' in process:
                return 'Coal Resources', 'Coal'
            elif 'GAS' in process:
                return 'Natural Gas Resources', 'Natural Gas'
            elif 'OIL' in process:
                return 'Oil Resources', 'Oil'
            elif 'NUC' in process:
                return 'Nuclear Resources', 'Nuclear Fuel'
            elif 'RNW' in process:
                return 'Renewable Resources', 'ELCRNW'
        elif process.startswith('IMP'):
            if 'COA' in process:
                return 'Coal Imports', 'Coal'
            elif 'GAS' in process:
                return 'Gas Imports', 'Natural Gas'
            elif 'OIL' in process:
                return 'Oil Imports', 'Oil'
            # Note: typically there are no "imports" for nuclear resources or renewables in VAR_Act
        
        # Fuel transformation processes (create intermediate commodities) - CHECK FIRST
        elif process.startswith('FTE-'):
            if 'ELCCOA' in process:
                return 'Coal', 'ELCCOA'
            elif 'ELCGAS' in process:
                return 'Natural Gas', 'ELCGAS'
            elif 'ELCOIL' in process:
                return 'Oil', 'ELCOIL'
            elif 'ELCNUC' in process:
                return 'Nuclear Fuel', 'ELCNUC'
            elif 'ELCRNW' in process:
                return 'Renewable Resources', 'Renewable Electricity'
            elif 'RSDGAS' in process:
                return 'Natural Gas', 'RSDGAS'
            elif 'TRAOIL' in process:
                return 'Oil', 'TRAOIL'
        
        # Electricity generation from existing plants
        elif 'ELCTE' in process and 'COA' in process:
            return 'ELCCOA', 'Electricity'
        elif 'ELCTE' in process and 'GAS' in process:
            return 'ELCGAS', 'Electricity'
        elif 'ELCTE' in process and 'OIL' in process:
            return 'ELCOIL', 'Electricity'
        elif 'ELCTE' in process and 'NUC' in process:
            return 'ELCNUC', 'Electricity'
        elif 'ELCRE' in process or ('ELC' in process and 'RNW' in process):
            return 'Renewable Electricity', 'Electricity'
        
        # Electricity generation from new plants
        elif 'ELCTN' in process and 'COA' in process:
            return 'ELCCOA', 'Electricity'
        elif 'ELCTN' in process and 'GAS' in process:
            return 'ELCGAS', 'Electricity'
        elif 'ELCTN' in process and 'OIL' in process:
            return 'ELCOIL', 'Electricity'
        elif 'ELCTN' in process and 'NUC' in process:
            return 'ELCNUC', 'Electricity'
        
        # Demand processes
        elif process.startswith('DTP') and 'COA' in process:
            return 'Coal', 'Industrial Demand'
        elif process.startswith('DTP') and 'ELC' in process:
            return 'Electricity', 'Electricity Demand'
        
        # Exports (energy leaving system)
        elif process.startswith('EXP') and 'COA' in process:
            return 'Coal', 'Coal Exports'
        elif process.startswith('EXP') and 'GAS' in process:
            return 'Natural Gas', 'Gas Exports'
        elif process.startswith('EXP') and 'OIL' in process:
            return 'Oil', 'Oil Exports'
        
        # End-use processes (consume intermediate commodities)
        elif 'ROT' in process or 'RSD' in process:
            return 'RSDGAS', 'Residential Heat'
        elif 'TOT' in process or 'TRA' in process:
            return 'TRAOIL', 'Transport'
        
        return None, None
    
    def _generate_node_colors(self, nodes):
        """Generate colors for energy nodes"""
        colors = []
        for node in nodes:
            node_lower = node.lower()
            if 'coal' in node_lower:
                colors.append('rgba(139,69,19,0.8)')  # Brown
            elif 'gas' in node_lower:
                colors.append('rgba(0,0,255,0.8)')    # Blue
            elif 'nuclear' in node_lower or 'uranium' in node_lower:
                colors.append('rgba(255,0,0,0.8)')    # Red
            elif 'oil' in node_lower:
                colors.append('rgba(0,0,0,0.8)')      # Black
            elif 'renewable' in node_lower or 'rnw' in node_lower:
                colors.append('rgba(0,255,0,0.8)')    # Green
            elif 'electricity' in node_lower:
                colors.append('rgba(255,255,0,0.8)')  # Yellow
            elif 'elc' in node_lower:
                colors.append('rgba(255,255,0,0.8)')  # Yellow for electricity generation nodes
            elif 'rsd' in node_lower:
                colors.append('rgba(0,0,255,0.8)')    # Blue for residential gas
            elif 'tra' in node_lower:
                colors.append('rgba(0,0,0,0.8)')      # Black for transport oil
            elif 'demand' in node_lower or 'heat' in node_lower or 'transport' in node_lower:
                colors.append('rgba(128,128,128,0.8)') # Gray
            elif 'export' in node_lower or 'import' in node_lower:
                colors.append('rgba(173,216,230,0.8)') # Light blue
            elif 'resources' in node_lower:
                colors.append('rgba(160,82,45,0.8)')   # Saddle brown for resource nodes
            else:
                colors.append('rgba(128,128,128,0.8)') # Default gray
        return colors
    
    def _generate_flow_colors(self, flows):
        """Generate colors for flows based on process type and technology classification"""
        colors = []
        for flow in flows:
            process_name = flow.get('process', '').upper()
            color = self._get_process_color(process_name)
            colors.append(color)
        return colors
    
    def _find_en_pairs(self, process_names):
        """Find pairs of processes that differ by only one character (E vs N)"""
        en_pairs = {}
        # Filter out None values and convert to strings
        process_list = [str(p) for p in process_names if p is not None and str(p).strip()]
        
        
        
        for i, process1 in enumerate(process_list):
            for j, process2 in enumerate(process_list):
                if i >= j:  # Avoid duplicate comparisons
                    continue
                
                # Check if processes differ by exactly one character
                if len(process1) == len(process2):
                    differences = []
                    for k in range(len(process1)):
                        if process1[k] != process2[k]:
                            differences.append((k, process1[k], process2[k]))
                    
                    # If exactly one character differs
                    if len(differences) == 1:
                        pos, char1, char2 = differences[0]
                        
                        # Check if the difference is E vs N (in either direction)
                        if (char1 == 'E' and char2 == 'N') or (char1 == 'N' and char2 == 'E'):
                            
                            # Store the pair, with N version as "new"
                            if char1 == 'N':
                                en_pairs[process1] = process2  # process1 is new, process2 is existing
                            else:
                                en_pairs[process2] = process1  # process2 is new, process1 is existing
        
        return en_pairs
    
    def _is_new_technology(self, process_name):
        """Detect if a process represents new technology based on E/N letter differences"""
        # Use cached E/N pairs if available
        if self._en_pairs_cache is None:
            # Get all process names from the data
            all_processes = set()
            for year, year_data in self.processed_data.items():
                if isinstance(year_data, dict) and 'flows' in year_data:
                    # Old format
                    for flow in year_data['flows']:
                        if 'process' in flow:
                            all_processes.add(flow['process'])
                else:
                    # New format - DataFrame
                    if hasattr(year_data, 'process'):
                        all_processes.update(year_data['process'].unique())
            
            # Find E/N pairs and cache them
            self._en_pairs_cache = self._find_en_pairs(all_processes)
            
            
        
        # Check if this process is marked as "new" in any E/N pair
        if process_name in self._en_pairs_cache:
            return True
        
        # Look for explicit NEW patterns
        if 'NEW' in process_name.upper():
            return True
        
        # Default to existing
        return False
    
    def _get_process_color(self, process_name):
        """Get color for a process - simple grey scheme"""
        if self._is_new_technology(process_name):
            return 'rgba(100,100,100,0.6)'   # Dark grey for new
        else:
            return 'rgba(180,180,180,0.6)'  # Light grey for existing (default)
    
    def _check_energy_balance(self, nodes, flows, year, unit='PJ', print_summary=False):
        """Check energy balance for each node and calculate conversion efficiencies.
        If print_summary is True, only the final DataFrame is printed; otherwise no stdout output.
        """
        unit_label = self._get_unit_label(unit)
        
        # Calculate inflows and outflows for each node
        node_balance = defaultdict(lambda: {'inflow': 0, 'outflow': 0, 'processes_in': [], 'processes_out': []})
        
        for flow in flows:
            source = flow['source']
            target = flow['target']
            value = flow['value']
            process = flow['process']
            
            # Outflow from source
            node_balance[source]['outflow'] += value
            node_balance[source]['processes_out'].append((process, value))
            
            # Inflow to target
            node_balance[target]['inflow'] += value
            node_balance[target]['processes_in'].append((process, value))
        
        # Collect data for intermediate nodes only
        intermediate_data = []
        
        # Collect balance for each node (no per-node printing)
        for node in sorted(nodes):
            balance = node_balance[node]
            inflow = balance['inflow']
            outflow = balance['outflow']
            net_balance = inflow - outflow
            
            # Convert values to display units
            inflow_display = self._convert_units(inflow, unit)
            outflow_display = self._convert_units(outflow, unit)
            net_balance_display = self._convert_units(net_balance, unit)
            
            # Skip balance check for extreme nodes (sources have only outflow, sinks have only inflow)
            is_source = inflow == 0 and outflow > 0  # Only outflow
            is_sink = outflow == 0 and inflow > 0    # Only inflow
            
            if not is_source and not is_sink:
                # Only calculate balance and efficiency for intermediate nodes
                efficiency = None
                if inflow > 0 and outflow > 0:
                    efficiency = (outflow / inflow) * 100
                
                # Imbalance for intermediate nodes
                imbalance_pct = None
                if abs(net_balance) > 0.1 and inflow > 0:
                    imbalance_pct = abs(net_balance) / inflow * 100
                
                # Collect data for intermediate nodes
                intermediate_data.append({
                    'Node': node,
                    'Inflow': inflow_display,
                    'Outflow': outflow_display,
                    'Balance': net_balance_display,
                    'Efficiency_%': efficiency,
                    'Imbalance_%': imbalance_pct,
                    'Unit': unit_label
                })
        
        # Create and display DataFrame for intermediate nodes
        if intermediate_data:
            df = pd.DataFrame(intermediate_data)
            if print_summary:
                print(df.to_string(index=False, float_format='%.1f'))
            return df
        else:
            empty_df = pd.DataFrame()
            if print_summary:
                print(empty_df.to_string(index=False))
            return empty_df
    
    def _get_user_friendly_label(self, node_name, unit='PJ'):
        """Convert raw variable names to user-friendly labels without units in parentheses"""
        label_mapping = {
            # Primary resources
            'Coal Resources': 'Coal Resources',
            'Natural Gas Resources': 'Natural Gas Resources', 
            'Oil Resources': 'Oil Resources',
            'Nuclear Resources': 'Nuclear Resources',
            'Renewable Resources': 'Renewable Resources',
            'Coal Imports': 'Coal Imports',
            'Gas Imports': 'Gas Imports',
            'Oil Imports': 'Oil Imports',
            
            # Primary commodities
            'Coal': 'Coal',
            'Natural Gas': 'Natural Gas',
            'Oil': 'Oil',
            'Nuclear Fuel': 'Nuclear Fuel',
            'Renewables': 'Renewables',
            
            # Intermediate electricity commodities
            'ELCCOA': 'Coal Electricity',
            'ELCGAS': 'Gas Electricity',
            'ELCOIL': 'Oil Electricity',
            'ELCNUC': 'Nuclear Electricity',
            'ELCRNW': 'Renewable Electricity',
            
            # Intermediate fuel commodities
            'RSDGAS': 'Residential Gas',
            'TRAOIL': 'Transport Fuel',
            
            # Final demand
            'Electricity': 'Electricity',
            'Electricity Demand': 'Electricity Demand',
            'Residential Heat': 'Residential Heat',
            'Transport': 'Transport',
            'Industrial Demand': 'Industrial Demand',
            
            # Exports
            'Coal Exports': 'Coal Exports',
            'Gas Exports': 'Gas Exports',
            'Oil Exports': 'Oil Exports'
        }
        
        return label_mapping.get(node_name, node_name)
    
    def _create_plotly_sankey(self, nodes, source_indices, target_indices, values, flows, year, unit='PJ'):
        """Create Plotly Sankey diagram"""
        # Use the centralized color generation method
        node_colors = self._generate_node_colors(nodes)
        
        # Convert node names to user-friendly labels with units
        friendly_labels = [self._get_user_friendly_label(node, unit) for node in nodes]
        
        # Convert values to target unit
        converted_values = [self._convert_units(value, unit) for value in values]
        
        # Create tooltips with values and units
        tooltips = []
        for i, value in enumerate(values):
            source_label = friendly_labels[source_indices[i]]
            target_label = friendly_labels[target_indices[i]]
            converted_value = self._convert_units(value, unit)
            unit_label = self._get_unit_label(unit)
            tooltip = f"{source_label} → {target_label}<br>Value: {converted_value:.1f} {unit_label}"
            tooltips.append(tooltip)
        
        # Get unit label for node tooltips
        unit_label = self._get_unit_label(unit)
        
        # Generate flow colors based on process type
        flow_colors = self._generate_flow_colors(flows)
        
        fig = go.Figure(data=[go.Sankey(
            node=dict(
                pad=15,
                thickness=20,
                line=dict(color="black", width=0.5),
                label=friendly_labels,
                color=node_colors,
                hovertemplate=f'%{{label}}<br>Units: {unit_label}<extra></extra>'
            ),
            link=dict(
                source=source_indices,
                target=target_indices,
                value=converted_values,
                color=flow_colors,
                hovertemplate='%{customdata}<extra></extra>',
                customdata=tooltips
            )
        )])
        
        unit_label = self._get_unit_label(unit)
        fig.update_layout(
            title_text=f"TIMES Energy Flow Diagram - {year} (Units: {unit_label})",
            font_size=12,
            width=1200,
            height=800
        )
        
        return fig
    
    def create_capacity_bar_plot(self, years_to_plot=None, show=False):
        """Create bar plot showing technology capacities in MW for each year"""
        if not years_to_plot:
            years_to_plot = [year for year in self.processed_data.keys() if year <= 2050]
        
        print(f"\nCreating capacity bar plot for years: {years_to_plot}")
        
        # Extract capacity data for power generation technologies
        capacity_data = []
        
        for year in years_to_plot:
            if year not in self.processed_data:
                continue
                
            year_data = self.processed_data[year]
            cap_data = year_data[year_data['variable'] == 'VAR_Cap'].copy()
            
            for _, row in cap_data.iterrows():
                process = row['process']
                value_mw = row['value']  # TIMES capacity typically in MW
                
                # Only include power generation technologies
                tech_type = self._get_technology_type(process)
                if tech_type:
                    capacity_data.append({
                        'year': year,
                        'technology': tech_type,
                        'process': process,
                        'capacity_mw': value_mw
                    })
        
        if not capacity_data:
            print("No capacity data found for power generation technologies")
            return None
        
        # Create DataFrame and aggregate by technology type
        df = pd.DataFrame(capacity_data)
        
        # Group by year and technology, sum capacities
        df_grouped = df.groupby(['year', 'technology'])['capacity_mw'].sum().reset_index()
        
        # Create bar plot
        fig = go.Figure()
        
        technologies = df_grouped['technology'].unique()
        colors = {
            'Coal Power': 'rgba(139,69,19,0.8)',
            'Gas Power': 'rgba(0,0,255,0.8)',
            'Nuclear Power': 'rgba(255,0,0,0.8)',
            'Oil Power': 'rgba(0,0,0,0.8)',
            'Renewable Power': 'rgba(0,255,0,0.8)',
            'Other': 'rgba(128,128,128,0.8)'
        }
        
        for tech in technologies:
            tech_data = df_grouped[df_grouped['technology'] == tech]
            fig.add_trace(go.Bar(
                x=tech_data['year'],
                y=tech_data['capacity_mw'],
                name=tech,
                marker_color=colors.get(tech, 'rgba(128,128,128,0.8)')
            ))
        
        fig.update_layout(
            title='Power Generation Capacity by Technology (MW)',
            xaxis_title='Year',
            yaxis_title='Capacity (MW)',
            barmode='stack',
            width=1000,
            height=600,
            font_size=12
        )
        
        print(f"Created capacity bar plot with {len(technologies)} technology types")
        if show:
            try:
                fig.show()
            except Exception:
                pass
        
        return fig
    
    def export_sankey_data_to_csv(self, year, flow_threshold=10.0,unit='PJ'):
        """Export Sankey data for a specific year to CSV files in both PJ and TWh"""
        if year not in self.processed_data:
            print(f"Year {year} not found in processed data")
            return
        
        print(f"Exporting Sankey data for year {year}...")
        
        # Get the Sankey data for the specified year
        year_data = self.processed_data[year]
        act_data = year_data[year_data['variable'] == 'VAR_Act'].copy()
        
        # Create combined data (nodes and flows)
        combined_data = []
        
        # First, collect all unique nodes and aggregate flows by process
        nodes = set()
        aggregated_flows = {}
        
        for _, row in act_data.iterrows():
            process = row['process']
            value = row['value']
            
            if value < flow_threshold:
                continue
            
            # Get flow direction
            flow_direction = self._get_energy_flow_direction(process)
            if not flow_direction:
                continue
            
            source, target = flow_direction
            nodes.add(source)
            nodes.add(target)
            
            # Create a key for aggregation (process + source + target)
            flow_key = f"{process}_{source}_{target}"
            
            if flow_key in aggregated_flows:
                # Sum values for the same process across time slices
                aggregated_flows[flow_key]['value_pj'] += value
                aggregated_flows[flow_key]['value_twh'] += self._convert_units(value, 'TWh')
            else:
                # Create new aggregated flow
                source_label = self._get_user_friendly_label(source)
                target_label = self._get_user_friendly_label(target)
                aggregated_flows[flow_key] = {
                    'times_variable': 'VAR_Act',
                    'times_process': process,
                    'source_node': source,
                    'target_node': target,
                    'source_label': source_label,
                    'target_label': target_label,
                    'value_pj': value,
                    'value_twh': self._convert_units(value, 'TWh')
                }
        
        # Convert aggregated flows to list
        flows_data = list(aggregated_flows.values())
        
        # Add nodes to combined data (filter out None values)
        for node in sorted([n for n in nodes if n is not None]):
            node_label = self._get_user_friendly_label(node)
            combined_data.append({
                'type': 'node',
                'times_variable': '-',
                'times_process': '-',
                'label': node_label,
                'technology_type': '-',
                'value_pj': '-',
                'value_twh': '-'
            })
        
        # Add flows to combined data
        for flow in flows_data:
            flow_label = f"{flow['source_label']} → {flow['target_label']}"
            # Determine if this is a new or existing technology
            technology_type = 'new' if self._is_new_technology(flow['times_process']) else 'existing'
            combined_data.append({
                'type': 'flow',
                'times_variable': flow['times_variable'],
                'times_process': flow['times_process'],
                'label': flow_label,
                'technology_type': technology_type,
                'value_pj': flow['value_pj'],
                'value_twh': flow['value_twh']
            })
        
        # Create DataFrame
        df = pd.DataFrame(combined_data)
        
        if df.empty:
            print(f"No data found for year {year} above threshold {flow_threshold}")
            return
        
        # Export PJ version
        pj_df = df[['type', 'times_variable', 'times_process', 'label', 'technology_type', 'value_pj']].copy()
        pj_df.columns = ['type', 'times_variable', 'times_process', 'label', 'technology_type', 'value_pj']
        pj_filename = f"../output/sankey_data_{year}_pj.csv"
        pj_df.to_csv(pj_filename, index=False)
        print(f"PJ data exported to: {pj_filename}")
        
        # Export TWh version
        twh_df = df[['type', 'times_variable', 'times_process', 'label', 'technology_type', 'value_twh']].copy()
        twh_df.columns = ['type', 'times_variable', 'times_process', 'label', 'technology_type', 'value_twh']
        twh_filename = f"../output/sankey_data_{year}_twh.csv"
        twh_df.to_csv(twh_filename, index=False)
        print(f"TWh data exported to: {twh_filename}")
        
        num_nodes = len(nodes)
        num_flows = len(flows_data)
        print(f"Exported {num_nodes} nodes and {num_flows} flows for year {year}")
        if unit == 'TWh':
            return twh_df
        else:
            return pj_df
    
    def _get_technology_type(self, process_name):
        """Classify process into technology type for capacity plotting"""
        process = process_name.upper()
        
        # Power generation technologies only
        if 'ELC' in process and ('TE' in process or 'TN' in process or 'RE' in process):
            if 'COA' in process:
                return 'Coal Power'
            elif 'GAS' in process:
                return 'Gas Power'
            elif 'NUC' in process:
                return 'Nuclear Power'
            elif 'OIL' in process:
                return 'Oil Power'
            elif 'RNW' in process or 'RER' in process:
                return 'Renewable Power'
        
        # Skip demand processes, mining, imports/exports, etc.
        return None


def main():
    """Main function to run the energy Sankey diagram generation"""
    
    # File paths
    filename="demos_004_0209.vd"
    vd_file = "./data/"+filename
    cache_file = "./output/"+filename+".pkl"
    
    print("TIMES Energy Flow Sankey Diagram Generator")
    print("=" * 50)
    
    # Initialize processor
    processor = TIMESEnergyFlowProcessor(vd_file, cache_path=cache_file)
    
    # Data is loaded automatically by the constructor
    
    # Get available years
    years = list(processor.processed_data.keys())
    if not years:
        print("No data found!")
        return
    
    print(f"\nAvailable years: {years}")
    

    # Create interactive Sankey diagrams in both units
    print(f"\n{'='*60}")
    print("Creating Interactive Energy Sankey Diagrams...")
    
    # Create PJ version
    interactive_fig_pj = processor.create_interactive_sankey(years, flow_threshold=10.0, unit='PJ', show=True)
    if interactive_fig_pj:
        interactive_output_pj = "../output/interactive_energy_sankey_pj.html"
        interactive_fig_pj.write_html(interactive_output_pj)
        print(f"Interactive Sankey diagram (PJ) saved to: {interactive_output_pj}")
    
    # Create TWh version
    interactive_fig_twh = processor.create_interactive_sankey(years, flow_threshold=10.0, unit='TWh', show=True)
    if interactive_fig_twh:
        interactive_output_twh = "../output/interactive_energy_sankey_twh.html"
        interactive_fig_twh.write_html(interactive_output_twh)
        print(f"Interactive Sankey diagram (TWh) saved to: {interactive_output_twh}")
    
    # Create capacity bar plot
    print(f"\n{'='*60}")
    print("Creating Power Generation Capacity Bar Plot...")
    
    capacity_fig = processor.create_capacity_bar_plot(years, show=True)
    
    if capacity_fig:
        # Save as HTML
        capacity_output = "../output/power_capacity_by_technology.html"
        capacity_fig.write_html(capacity_output)
        print(f"Capacity bar plot saved to: {capacity_output}")
    
    # Export Sankey data to CSV files
    print(f"\n{'='*60}")
    print("Exporting Sankey Data to CSV Files...")
    
    export_year = 2020  # Parameter to define the year for CSV export
    if export_year in years:
        sankey_df = processor.export_sankey_data_to_csv(export_year, unit='TWh')
        print(f"Sankey data for {export_year} exported to CSV files")
    else:
        print(f"Year {export_year} not available in data. Available years: {years}")
    
    print("\nDone! Check the generated files for:")
    print("  - Interactive Energy Sankey (PJ): output/interactive_energy_sankey_pj.html")
    print("  - Interactive Energy Sankey (TWh): output/interactive_energy_sankey_twh.html")
    print("  - Capacity bar plot (MW): output/power_capacity_by_technology.html")
    print(f"  - Sankey data CSV (PJ): output/sankey_data_{export_year}_pj.csv")
    print(f"  - Sankey data CSV (TWh): output/sankey_data_{export_year}_twh.csv")


if __name__ == "__main__":
    main()