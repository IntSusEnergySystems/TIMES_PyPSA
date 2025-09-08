import pandas as pd
import pickle
import os

class TIMESOutputReader:
    def __init__(self, vd_file_path, cache_path='output/data_cache.pkl'):
        self.vd_file_path = vd_file_path
        self.cache_path = cache_path
        self.raw_data = []
        self.processed_data = {}
        self.load_data()

    def load_data(self):
        """Load data from cache or parse the .vd file if cache is not available or outdated."""
        if os.path.exists(self.cache_path):
            print(f"Loading cached data from {self.cache_path}...")
            with open(self.cache_path, 'rb') as f:
                self.processed_data = pickle.load(f)
            # Potentially add a check for file modification time to see if cache is outdated
        else:
            self.parse_vd_file()
            self.process_data_by_year()
            print(f"Saving cached data to {self.cache_path}...")
            with open(self.cache_path, 'wb') as f:
                pickle.dump(self.processed_data, f)

    def parse_vd_file(self):
        """Parse the .vd file and extract data"""
        print(f"Parsing {self.vd_file_path}...")
        
        with open(self.vd_file_path, 'r') as file:
            lines = file.readlines()
        
        # Skip header lines (starting with *)
        for line in lines:
            line = line.strip()
            if not line or line.startswith('*'):
                continue
                
            try:
                # Parse CSV-like format with quoted strings
                parts = []
                current_part = ""
                in_quotes = False
                
                for char in line:
                    if char == '"':
                        in_quotes = not in_quotes
                    elif char == ',' and not in_quotes:
                        parts.append(current_part.strip('"'))
                        current_part = ""
                    else:
                        current_part += char
                
                parts.append(current_part.strip('"'))
                
                if len(parts) >= 9:
                    variable = parts[0]
                    attribute = parts[1] if parts[1] != "-" else None
                    process = parts[2] if parts[2] != "-" else None
                    period = parts[3] if parts[3] != "-" else None
                    region = parts[4] if parts[4] != "-" else None
                    vintage = parts[5] if parts[5] != "-" else None
                    timeslice = parts[6] if parts[6] != "-" else None
                    constraint = parts[7] if parts[7] != "-" else None
                    value = float(parts[8]) if parts[8] != "-" else 0.0
                    
                    self.raw_data.append({
                        'variable': variable,
                        'attribute': attribute,
                        'process': process,
                        'period': period,
                        'region': region,
                        'vintage': vintage,
                        'timeslice': timeslice,
                        'constraint': constraint,
                        'value': value
                    })
            
            except Exception as e:
                continue
        
        print(f"Parsed {len(self.raw_data)} data records")
        return self.raw_data

    def process_data_by_year(self):
        """Process raw data and organize by year"""
        print("Processing data by year...")
        
        df = pd.DataFrame(self.raw_data)
        df['year'] = pd.to_numeric(df['period'], errors='coerce')
        df = df.dropna(subset=['year'])
        df['year'] = df['year'].astype(int)
        
        years = sorted(df['year'].unique())
        print(f"Found data for years: {years}")
        
        for year in years:
            year_data = df[df['year'] == year].copy()
            self.processed_data[year] = year_data
        
        return self.processed_data
