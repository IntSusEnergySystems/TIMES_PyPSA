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
        'solar', 'wind', 'hydro', 'nuclear', 'nuc', 'uran', 'uranium', 'fuel', 'hydrogen'
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


def filter_for_sankey(annual_df, commodities_df, year, mapping_df=None):
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

    # Determine energy commodity codes strictly from mapping Unit == 'PJ'
    mapping_energy_codes = set()
    if mapping_df is not None and not mapping_df.empty and 'unit' in mapping_df.columns and 'times' in mapping_df.columns:
        mapping_energy_codes = set(mapping_df.loc[mapping_df['unit'].astype(str).str.strip().str.upper() == 'PJ', 'times'].astype(str).str.strip().unique())

    # If mapping is present, filter by those codes; otherwise, fall back to all observed
    if mapping_energy_codes and 'commodity_code' in df.columns:
        df = df[df['commodity_code'].astype(str).isin(mapping_energy_codes)]

    return df, mapping_energy_codes


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


# -----------------------------
# Process clustering utilities
# -----------------------------
def compute_process_metrics(df):
    """
    Compute per-process metrics: inflow sum, outflow sum, and counts of
    distinct commodities on in/out. Returns a DataFrame indexed by process.
    """
    if df.empty:
        return pd.DataFrame(columns=['process_code', 'in_sum', 'out_sum', 'num_in', 'num_out'])

    dfc = df.copy()
    dfc['var_u'] = dfc['variable'].str.upper()

    fin = dfc[dfc['var_u'] == 'VAR_FIN']
    fout = dfc[dfc['var_u'] == 'VAR_FOUT']

    in_sum = fin.groupby('process_code', as_index=False)['value'].sum().rename(columns={'value': 'in_sum'})
    out_sum = fout.groupby('process_code', as_index=False)['value'].sum().rename(columns={'value': 'out_sum'})
    num_in = fin.groupby('process_code', as_index=False)['commodity_code'].nunique().rename(columns={'commodity_code': 'num_in'})
    num_out = fout.groupby('process_code', as_index=False)['commodity_code'].nunique().rename(columns={'commodity_code': 'num_out'})

    metrics = (
        pd.DataFrame({'process_code': pd.concat([in_sum['process_code'], out_sum['process_code']]).unique()})
        .merge(in_sum, on='process_code', how='left')
        .merge(out_sum, on='process_code', how='left')
        .merge(num_in, on='process_code', how='left')
        .merge(num_out, on='process_code', how='left')
    )
    for c in ['in_sum', 'out_sum', 'num_in', 'num_out']:
        metrics[c] = metrics[c].fillna(0)
    return metrics


def detect_series_clusters(df, metrics, tolerance=1e-3):
    """
    Detect chains of processes with single in and single out, and near-zero
    losses, which can be merged as series clusters.
    Returns a list of clusters, each an ordered list of process codes.
    """
    if df.empty or metrics.empty:
        return []

    dfx = df.copy()
    dfx['var_u'] = dfx['variable'].str.upper()
    fin = dfx[dfx['var_u'] == 'VAR_FIN']
    fout = dfx[dfx['var_u'] == 'VAR_FOUT']

    candidates = metrics[(metrics['num_in'] == 1) & (metrics['num_out'] == 1)].copy()
    candidates = candidates[(candidates['in_sum'] + candidates['out_sum']) > 0]
    candidates = candidates[(abs(candidates['in_sum'] - candidates['out_sum']) <= tolerance * candidates[['in_sum', 'out_sum']].max(axis=1))]
    candidate_set = set(candidates['process_code'])
    if not candidate_set:
        return []

    fin_pc = fin.groupby(['process_code', 'commodity_code'], as_index=False)['value'].sum()
    fout_pc = fout.groupby(['process_code', 'commodity_code'], as_index=False)['value'].sum()
    fin_map = {(r['process_code'], r['commodity_code']): r['value'] for _, r in fin_pc.iterrows()}
    fout_map = {(r['process_code'], r['commodity_code']): r['value'] for _, r in fout_pc.iterrows()}

    in_comm = fin.groupby('process_code')['commodity_code'].first().to_dict()
    out_comm = fout.groupby('process_code')['commodity_code'].first().to_dict()

    prod_by_comm = fout.groupby('commodity_code')['process_code'].apply(set).to_dict()
    cons_by_comm = fin.groupby('commodity_code')['process_code'].apply(set).to_dict()

    next_proc = {}
    indeg = {p: 0 for p in candidate_set}
    for p in candidate_set:
        c_out = out_comm.get(p)
        if c_out is None:
            continue
        cons = cons_by_comm.get(c_out, set()) & candidate_set
        if len(cons) == 1:
            q = list(cons)[0]
            if p != q:
                v_out = fout_map.get((p, c_out), 0.0)
                v_in = fin_map.get((q, c_out), 0.0)
                denom = max(v_out, v_in, 1e-12)
                if abs(v_out - v_in) <= tolerance * denom:
                    next_proc[p] = q
                    indeg[q] = indeg.get(q, 0) + 1

    visited = set()
    clusters = []
    for p in candidate_set:
        if p in visited:
            continue
        if indeg.get(p, 0) == 0:
            chain = [p]
            visited.add(p)
            cur = p
            while cur in next_proc and next_proc[cur] not in visited:
                cur = next_proc[cur]
                chain.append(cur)
                visited.add(cur)
            if len(chain) > 1:
                clusters.append(chain)

    return clusters

def net_bidirectional_links(df):
    """
    Net out bidirectional links between the same process (or cluster) and
    commodity, so that internal hand-offs within clusters do not create
    artificial loops. This is general and applies whether clustering is used
    or not.

    For each (year, region, process_code, commodity_code):
      net = sum(VAR_FOut values) - sum(VAR_FIn values)
    - If net > 0, keep a single VAR_FOut with 'net'
    - If net < 0, keep a single VAR_FIn with 'abs(net)'
    - If net == 0, drop the pair (purely internal transfer)
    """
    if df.empty:
        return df

    d = df.copy()
    d['var_u'] = d['variable'].str.upper()
    d['signed'] = d.apply(lambda r: r['value'] if r['var_u'] == 'VAR_FOUT' else (-r['value'] if r['var_u'] == 'VAR_FIN' else 0.0), axis=1)

    agg = (
        d.groupby(['year', 'region', 'process_code', 'commodity_code'], as_index=False)['signed']
        .sum()
    )
    agg = agg[agg['signed'] != 0]
    if agg.empty:
        # All cancelled out; return empty with expected columns
        cols = ['year', 'region', 'variable', 'commodity_code', 'commodity', 'process_code', 'process', 'value']
        return pd.DataFrame(columns=cols)

    agg['variable'] = agg['signed'].apply(lambda v: 'VAR_FOut' if v > 0 else 'VAR_FIn')
    agg['value'] = agg['signed'].abs()
    agg = agg.drop(columns=['signed'])

    # Attach readable names from the original df
    comm_map = d[['commodity_code', 'commodity']].dropna().drop_duplicates().set_index('commodity_code')['commodity'].to_dict()
    proc_map = d[['process_code', 'process']].dropna().drop_duplicates().set_index('process_code')['process'].to_dict()

    agg['commodity'] = agg['commodity_code'].map(lambda c: comm_map.get(c, c))
    agg['process'] = agg['process_code'].map(lambda p: proc_map.get(p, p))

    # Reorder columns
    agg = agg[['year', 'region', 'variable', 'commodity_code', 'commodity', 'process_code', 'process', 'value']]
    return agg


# -----------------------------
# Mapping-based process clustering (simple aggregation by column)
# -----------------------------
def _find_case_insensitive_column(df, target_name):
    for c in df.columns:
        if c.strip().lower() == str(target_name).strip().lower():
            return c
    return None


def _majority_or_first(values):
    vals = [str(v).strip() for v in values if str(v).strip() and str(v).strip().lower() != 'nan']
    if not vals:
        return None
    # Majority vote
    counts = {}
    for v in vals:
        counts[v] = counts.get(v, 0) + 1
    vals_sorted = sorted(counts.items(), key=lambda x: (-x[1], x[0]))
    return vals_sorted[0][0]


def apply_mapping_based_process_clustering(df, processes_df, agg_column_name, process_unit_col=None):
    """
    Replace each process by an aggregated process according to a column in
    mapping_processes.csv (e.g., "Aggregation Level 1"). No other clustering
    is performed. Returns (clustered_df, aggregated_process_unit_map).
    """
    if df.empty:
        return df, {}

    # Resolve the aggregation column name case-insensitively
    agg_col = _find_case_insensitive_column(processes_df, agg_column_name)
    if agg_col is None:
        print(f"[INFO] Aggregation column '{agg_column_name}' not found in process mapping; skipping process clustering.")
        return df, {}

    # Ensure required columns exist
    if 'Process' not in processes_df.columns:
        raise KeyError("Process mapping DataFrame must contain a 'Process' column.")

    # Build mapping: process_code -> (cluster_code, cluster_name)
    proc_to_cluster_code = {}
    cluster_code_to_name = {}

    for _, row in processes_df.iterrows():
        pcode = str(row['Process']).strip()
        raw_label = str(row.get(agg_col, '')).strip()
        if raw_label and raw_label.lower() != 'nan':
            cluster_code = f"AGG_{_slugify(raw_label)}"
            cluster_name = raw_label
        else:
            cluster_code = pcode
            cluster_name = str(row.get('Description', pcode))
        proc_to_cluster_code[pcode] = cluster_code
        # Prefer first encountered non-empty name
        if cluster_code not in cluster_code_to_name:
            cluster_code_to_name[cluster_code] = cluster_name

    # Build aggregated unit map for tooltips
    aggregated_process_unit_map = {}
    if process_unit_col is not None and process_unit_col in processes_df.columns:
        # Group by cluster and choose majority/non-empty unit
        processes_df['_cluster_code_tmp'] = processes_df['Process'].map(lambda p: proc_to_cluster_code.get(str(p).strip(), str(p).strip()))
        unit_col_real = process_unit_col
        grouped = processes_df.groupby('_cluster_code_tmp')[unit_col_real].apply(list).to_dict()
        for cc, vals in grouped.items():
            chosen = _majority_or_first(vals)
            if chosen:
                aggregated_process_unit_map[cc] = chosen
        processes_df.drop(columns=['_cluster_code_tmp'], inplace=True)

    # Apply mapping to data
    dfc = df.copy()
    dfc['process_code'] = dfc['process_code'].astype(str).map(lambda p: proc_to_cluster_code.get(p, p))
    # Update readable process names to cluster names where applicable; fall back to original names
    new_names = dfc['process_code'].map(lambda p: cluster_code_to_name.get(p))
    dfc['process'] = new_names.where(new_names.notna(), dfc['process'])

    return dfc, aggregated_process_unit_map


# -----------------------------
# Commodity grouping utilities
# -----------------------------
def _slugify(name):
    s = (name or '').strip().lower()
    out = []
    for ch in s:
        if ch.isalnum():
            out.append(ch)
        elif ch in [' ', '-', '/', '(', ')', '&']:
            out.append('_')
    slug = ''.join(out)
    while '__' in slug:
        slug = slug.replace('__', '_')
    return slug.strip('_') or 'other'


def read_commodity_mapping_table(mapping_file):
    """
    Read mapping table from CSV and normalize column names.
    Expected columns (case/spacing-insensitive):
      - Energy Carrier - PYPSA
      - commodities TIMES
      - uspstream_commodity (optional)
      - upstream_process (optional)
      - Sector (com_in) (optional)
      - Comment (optional)
    """
    if not os.path.exists(mapping_file):
        return pd.DataFrame(columns=[
            'pypsa', 'times', 'upstream_commodity', 'upstream_process', 'sector', 'comment'
        ])

    df = pd.read_csv(mapping_file, engine='python')
    # Normalize columns
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

    # New header names: 'TIMES commodity', 'Description', 'Unit', 'Sector', 'Type',
    # 'PyPSA Energy Carrier', 'Upstream commodity', 'Upstream process', 'Comment'
    pypsa_col = get(['pypsa energy carrier', 'pypsa', 'energy_carrier_pypsa'])
    times_col = get(['times commodity', 'times_commodity', 'times', 'commodity'])
    desc_col = get(['description', 'desc'])
    unit_col = get(['unit'])
    upst_comm_col = get(['upstream commodity', 'uspstream_commodity', 'upstream_commodity'])
    upst_proc_col = get(['upstream process', 'upstream_process'])
    sector_col = get(['sector (com_in)', 'sector'])
    comment_col = get(['comment', 'comments'])

    n = len(df)
    def series_or_empty(col_name):
        if col_name is not None and col_name in df.columns:
            return df[col_name]
        return pd.Series([''] * n)

    out = pd.DataFrame({
        'pypsa': series_or_empty(pypsa_col),
        'times': series_or_empty(times_col),
        'description': series_or_empty(desc_col),
        'unit': series_or_empty(unit_col),
        'upstream_commodity': series_or_empty(upst_comm_col),
        'upstream_process': series_or_empty(upst_proc_col),
        'sector': series_or_empty(sector_col),
        'comment': series_or_empty(comment_col),
    })
    # Strip whitespace
    for c in out.columns:
        out[c] = out[c].astype(str).map(lambda x: x.strip())
    # Drop empty times codes
    out = out[out['times'] != '']
    return out


def write_commodity_mapping_table(mapping_file, mapping_df):
    cols = ['Energy Carrier - PYPSA', 'commodities TIMES ', 'uspstream_commodity', 'upstream_process', 'Sector (com_in)', 'Comment']
    df = pd.DataFrame({
        cols[0]: mapping_df['pypsa'],
        cols[1]: mapping_df['times'],
        cols[2]: mapping_df.get('upstream_commodity', ''),
        cols[3]: mapping_df.get('upstream_process', ''),
        cols[4]: mapping_df.get('sector', ''),
        cols[5]: mapping_df.get('comment', ''),
    })
    df.to_csv(mapping_file, index=False)


def build_commodity_groups_from_mapping(mapping_df, energy_commodity_codes, commodities_df, mapping_file):
    """
    Use mapping table to group TIMES commodity codes into PYPSA carriers.
    Adds missing energy commodities to mapping (auto-added) using heuristics
    for their PYPSA label and persists back to file.
    Returns (commodity_to_group, groups_info)
    """
    mapping_df = mapping_df.copy()
    mapping_df['pypsa'] = mapping_df['pypsa'].astype(str).map(lambda s: s.strip())
    mapping_df['times'] = mapping_df['times'].astype(str).map(lambda s: s.strip())

    # Build initial map from file
    times_to_pypsa = {row['times']: row['pypsa'] for _, row in mapping_df.iterrows() if row['times']}

    # Add missing energy commodities
    missing = [c for c in energy_commodity_codes if c not in times_to_pypsa]
    if missing:
        # If commodities_df is not provided (we are phasing out commodities.csv),
        # fall back to using mapping_df's Description column when adding new codes.
        comm_desc = commodities_df.set_index('Commodity')['Description'].to_dict() if commodities_df is not None and not commodities_df.empty else {}
        new_rows = []
        for code in missing:
            gid, gname = _categorize_commodity(code, comm_desc.get(code, ''))
            # Map heuristic name to a PYPSA label best-effort
            # Keep it simple: use the readable name as PYPSA label
            pypsa_name = gname
            print(f"[WARN] Commodity '{code}' missing from mapping table; inferred PYPSA='{pypsa_name}' and appended to {mapping_file} (comment=auto-added).")
            new_rows.append({
                'pypsa': pypsa_name,
                'times': code,
                'upstream_commodity': '',
                'upstream_process': '',
                'sector': '',
                'comment': 'auto-added'
            })
            times_to_pypsa[code] = pypsa_name

        if new_rows:
            mapping_df = pd.concat([mapping_df, pd.DataFrame(new_rows)], ignore_index=True)
            # Persist mapping back to CSV
            write_commodity_mapping_table(mapping_file, mapping_df)

    # Build groups
    groups = {}
    for code, pypsa_name in times_to_pypsa.items():
        gid = f"PYPSA_{_slugify(pypsa_name)}"
        if gid not in groups:
            groups[gid] = {'name': pypsa_name, 'type': 'commodity_group_pypsa', 'members': []}
        groups[gid]['members'].append(code)

    commodity_to_group = {}
    for gid, info in groups.items():
        for m in info['members']:
            commodity_to_group[m] = gid

    groups_info = {
        gid: {'name': info['name'], 'type': info['type'], 'members': sorted(info['members'])}
        for gid, info in groups.items()
    }
    return commodity_to_group, groups_info

def apply_commodity_grouping(df, commodity_to_group, groups_info, commodities_df):
    """
    Replace commodity codes by grouped category ids and attach readable names.
    """
    if df.empty or not commodity_to_group:
        return df
    dfg = df.copy()
    # Preserve original for traceability
    if 'commodity_code_orig' not in dfg.columns:
        dfg['commodity_code_orig'] = dfg['commodity_code']
    dfg['commodity_code'] = dfg['commodity_code'].map(lambda c: commodity_to_group.get(c, c))

    # Build name map: base commodity names + group names
    comm_desc = commodities_df.set_index('Commodity')['Description'].to_dict()
    name_map = {**{k: v for k, v in comm_desc.items()}, **{gid: info['name'] for gid, info in groups_info.items()}}
    dfg['commodity'] = dfg['commodity_code'].map(lambda c: name_map.get(c, c))
    return dfg


def build_sankey(df, output_html_file, year, flow_threshold=0.0, process_unit_map=None):
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
    # Direction based on variable type. Ensure nuclear fuel (e.g., ELCNUC, NUCRSV)
    # feeds into nuclear generation processes rather than electricity into ENUC.
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

    # Prepare labels using descriptions from the filtered data. Ensure grouped
    # nuclear commodities keep readable names.
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

    # Build tooltips that include source → target, commodity/cluster name and value (with process unit)
    node_label_map = {n: lbl for n, lbl in zip(nodes, labels)}
    tooltips = []
    for _, row in links_df.iterrows():
        s = row['source']
        t = row['target']
        v = float(row['value'])
        src = node_label_map.get(s, str(s))
        tgt = node_label_map.get(t, str(t))
        # Try to infer the commodity/cluster on the link
        comm_label = None
        if s in commodity_desc:
            comm_label = commodity_desc[s]
        elif t in commodity_desc:
            comm_label = commodity_desc[t]
        # Determine process unit from mapping if available
        proc_code = s if s in process_desc else (t if t in process_desc else None)
        unit = None
        if process_unit_map is not None and proc_code is not None:
            unit = process_unit_map.get(proc_code)
        unit_str = unit if unit and isinstance(unit, str) and unit.strip() else 'PJ'
        if comm_label:
            tooltip = f"{src} → {tgt}<br>Commodity: {comm_label}<br>Value: {v:.2f} {unit_str}"
        else:
            tooltip = f"{src} → {tgt}<br>Value: {v:.2f} {unit_str}"
        tooltips.append(tooltip)

    sankey_links = {
        'source': links_df['source'].map(node_index).tolist(),
        'target': links_df['target'].map(node_index).tolist(),
        'value': links_df['value'].tolist(),
        'customdata': tooltips,
        'hovertemplate': '%{customdata}<extra></extra>',
    }

    # Dynamically adjust node pad/thickness based on node counts to keep link widths readable
    left_nodes = set(n for n in nodes if n in commodity_desc)
    right_or_process_nodes = set(n for n in nodes if n in process_desc)
    n_max_col = max(len(left_nodes), len(right_or_process_nodes)) or 1
    # Heuristic: more nodes -> smaller pad/thickness
    dyn_pad = max(4, min(20, int(300 / n_max_col)))
    dyn_thickness = max(10, min(30, int(600 / n_max_col)))

    fig = go.Figure(data=[go.Sankey(
        node=dict(
            pad=dyn_pad,
            thickness=dyn_thickness,
            line=dict(color="black", width=0.5),
            label=labels,
        ),
        link=sankey_links
    )])

    fig.update_layout(
        title_text=f"Energy Flow Diagram - {year} (PJ)",
        font_size=10
    )
    fig.write_html(output_html_file)
    print(f"Saved Sankey diagram to {output_html_file}")
    return fig

def main():
    """Main function to generate the Sankey diagram."""

    # --- Simplification options ---
    cluster = True
    if cluster:
        # Set to False to build unclustered Sankey
        enable_process_clustering = True
        # Set to False to keep all commodity codes (no grouping)
        group_commodities = True
        # Max share of total final energy for the 'Others' buckets (0.10 = 10%)
        max_cluster_pct = 0.1
        # Column in mapping_processes.csv used for process aggregation
        process_cluster_column = "Aggregation Level 1"
    else:
        # Set to False to build unclustered Sankey
        enable_process_clustering = False
        # Set to False to keep all commodity codes (no grouping)
        group_commodities = False
        # Max share of total final energy for the 'Others' buckets (0.10 = 10%)
        max_cluster_pct = 0  
        process_cluster_column = None

    # --- Configuration ---
    vd_file = "data/bau_080925_0809.vd"
    selected_year = 2021
    # commodities_file removed in favor of mapping-based metadata
    # processes_file removed in favor of mapping-based metadata
    output_csv_file = f"output/annual_values{'_clustered' if cluster else ''}.csv"
    output_filtered_csv = f"output/annual_flows_{selected_year}_energy{'_clustered' if cluster else ''}.csv"
    output_html_file = f"output/bau_sankey_{selected_year}_pj{'_clustered' if cluster else ''}.html"
  

    os.makedirs("output", exist_ok=True)

    # --- Load metadata ---
    print("Loading metadata...")
    # Load process mapping and construct processes_df from mapping
    process_mapping_file = "data/mapping_processes.csv"
    processes_df = pd.read_csv(process_mapping_file)
    if 'Process' not in processes_df.columns and 'Technology (Process)' in processes_df.columns:
        processes_df = processes_df.rename(columns={'Technology (Process)': 'Process'})
    if 'Description' not in processes_df.columns:
        # Best-effort: if there is a 'description' in different case
        for c in processes_df.columns:
            if c.strip().lower() == 'description':
                processes_df = processes_df.rename(columns={c: 'Description'})
                break
    # Build unit lookup map for tooltips
    process_unit_col = None
    for c in processes_df.columns:
        if c.strip().lower() in ['activity unit', 'activity_unit', 'unit']:
            process_unit_col = c
            break
    process_unit_map = {}
    if process_unit_col is not None:
        process_unit_map = processes_df.set_index('Process')[process_unit_col].to_dict()
    # Load commodity mapping and construct commodities_df surrogate from mapping
    mapping_file = "data/mapping_commodities.csv"
    mapping_df = read_commodity_mapping_table(mapping_file)
    # Ensure 'description' column exists from the mapping file (case-normalized)
    if 'description' not in mapping_df.columns:
        # Try to fetch from original CSV casing
        try:
            raw_map = pd.read_csv(mapping_file, engine='python')
            if 'Description' in raw_map.columns and 'TIMES commodity' in raw_map.columns:
                mapping_df = mapping_df.merge(
                    raw_map[['TIMES commodity', 'Description']].rename(columns={'TIMES commodity': 'times'}),
                    on='times', how='left'
                )
                mapping_df = mapping_df.rename(columns={'Description': 'description'})
        except Exception:
            pass
    # Build commodities DataFrame with code and description from mapping
    commodities_df = pd.DataFrame({
        'Commodity': mapping_df['times'],
        'Description': mapping_df['description'] if 'description' in mapping_df.columns else ''
    })

    # --- Load raw records (no filtering by variable or commodity) ---
    raw_flows_df = load_raw_records(vd_file)

    if raw_flows_df.empty:
        print("No valid energy flow data was processed. Exiting.")
        return

    # --- Aggregate to annual ---
    annual_values_df = aggregate_to_annual(raw_flows_df)

    # --- Join descriptions ---
    annual_values_df = (
        annual_values_df
        .merge(commodities_df.rename(columns={"Commodity": "commodity_code", "Description": "commodity"}), on="commodity_code", how="left")
        .merge(processes_df.rename(columns={"Process": "process_code", "Description": "process"}), on="process_code", how="left")
    )

    # Reorder columns for readability
    ordered_cols = [
        'year', 'region', 'variable', 'commodity_code', 'commodity', 'process_code', 'process', 'value'
    ]
    for col in ordered_cols:
        if col not in annual_values_df.columns:
            annual_values_df[col] = None
    annual_values_df = annual_values_df[ordered_cols]

    # --- Save CSV ---
    print(f"Writing annual aggregated values to {output_csv_file} ...")
    annual_values_df.to_csv(output_csv_file, index=False)
    print("Done.")

    # --- Filter for Sankey (year=2021, VAR_F*, energy commodities) ---
    filtered_df, energy_codes = filter_for_sankey(annual_values_df, commodities_df, year=selected_year, mapping_df=mapping_df)
    if filtered_df.empty:
        print("Filtered dataset for Sankey is empty; skipping Sankey generation.")
        return

    # Before writing the filtered flows, attach columns showing the target clustered flow
    # - clustered_process_code / clustered_process (based on mapping_processes.csv column)
    # - grouped_commodity_code / grouped_commodity (based on commodity grouping if enabled)
    preview_df = filtered_df.copy()
    # Process clustering preview
    if enable_process_clustering and process_cluster_column:
        preview_clustered_df, _ = apply_mapping_based_process_clustering(
            preview_df, processes_df, agg_column_name=process_cluster_column, process_unit_col=process_unit_col
        )
        filtered_df['clustered_process_code'] = preview_clustered_df['process_code']
        filtered_df['clustered_process'] = preview_clustered_df['process']
    else:
        filtered_df['clustered_process_code'] = filtered_df['process_code']
        filtered_df['clustered_process'] = filtered_df['process']

    # Commodity grouping preview (only compute mapping here; JSON/CSV saving remains in the grouping block below)
    commodity_to_group = None
    groups_info = None
    if group_commodities:
        commodity_to_group, groups_info = build_commodity_groups_from_mapping(mapping_df, energy_codes, commodities_df, mapping_file)
        # Build name map for groups
        group_name_map = {gid: info['name'] for gid, info in groups_info.items()} if groups_info else {}
        filtered_df['grouped_commodity_code'] = filtered_df['commodity_code'].map(lambda c: commodity_to_group.get(c, c))
        filtered_df['grouped_commodity'] = filtered_df['grouped_commodity_code'].map(lambda gid: group_name_map.get(gid, filtered_df.set_index('commodity_code')['commodity'].to_dict().get(gid, gid)))
    else:
        filtered_df['grouped_commodity_code'] = filtered_df['commodity_code']
        filtered_df['grouped_commodity'] = filtered_df['commodity']

    print(f"Writing filtered flows to {output_filtered_csv} ...")
    filtered_df.to_csv(output_filtered_csv, index=False)
    print("Done.")

    # --- Optional: Group commodities to reduce node count ---
    if group_commodities:
        # Reuse computed groups if available from preview; else compute
        if not commodity_to_group or not groups_info:
            commodity_to_group, groups_info = build_commodity_groups_from_mapping(mapping_df, energy_codes, commodities_df, mapping_file)
        if groups_info:
            import json
            groups_json_file = f"output/sankey_commodity_groups_{selected_year}.json"
            groups_csv_file = f"output/sankey_commodity_groups_{selected_year}.csv"
            with open(groups_json_file, 'w') as f:
                json.dump(groups_info, f, indent=2)
            pd.DataFrame([
                {"group_id": gid, "name": info['name'], "type": info['type'], "members": ';'.join(info['members'])}
                for gid, info in groups_info.items()
            ]).to_csv(groups_csv_file, index=False)
            print(f"Saved commodity groups to {groups_json_file} and {groups_csv_file}")
            # Apply grouping to the dataset used for Sankey
            filtered_df = apply_commodity_grouping(filtered_df, commodity_to_group, groups_info, commodities_df)

    # --- Analyze connectivity and warn isolated processes ---
    both_io, only_in, only_out = analyze_process_connectivity(filtered_df)

    # --- Option: process clustering toggle ---
    if not enable_process_clustering:
        print("Process clustering disabled. Building unclustered Sankey.")
        # Represent all flows without netting
        _ = build_sankey(filtered_df, output_html_file, year=selected_year, flow_threshold=0.0, process_unit_map=process_unit_map)
    else:
        # --- Apply mapping-based process clustering (no other clustering) ---
        clustered_df, aggregated_unit_map = apply_mapping_based_process_clustering(
            filtered_df, processes_df, agg_column_name=process_cluster_column, process_unit_col=process_unit_col
        )
        # Merge unit maps: prefer aggregated units for aggregated codes, fall back to original map
        combined_unit_map = {**(process_unit_map or {}), **(aggregated_unit_map or {})}

        # --- Build Sankey ---
        _ = build_sankey(clustered_df, output_html_file, year=selected_year, flow_threshold=0.0, process_unit_map=combined_unit_map)

if __name__ == "__main__":
    main()
