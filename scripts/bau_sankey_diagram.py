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
    if 'ELC' in process_upper or 'ENUC' in process_upper:
        # Try to find input fuel among common carriers
        for c in ['COA', 'GAS', 'OIL', 'NUC', 'BIO', 'HYD', 'WIN', 'SOL']:
            if c in process_upper:
                # Map commodity-like code to readable source node
                fuel_map = {
                    'COA': 'Coal',
                    'GAS': 'Natural Gas',
                    'OIL': 'Oil',
                    'NUC': 'Nuclear Fuel',
                    'BIO': 'Biomass & Biofuels',
                    'HYD': 'Hydro',
                    'WIN': 'Wind',
                    'SOL': 'Solar',
                }
                return fuel_map.get(c, c), "Electricity"
        # If we cannot detect explicit fuel, default to generic generation
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


def longest_common_substring(strings):
    if not strings:
        return ''
    base = strings[0]
    others = strings[1:]
    longest = ''
    n = len(base)
    for i in range(n):
        for j in range(i + 1, n + 1):
            sub = base[i:j]
            if len(sub) <= len(longest):
                continue
            if all(sub in s for s in others):
                longest = sub
    return longest if len(longest) >= 3 else ''


def build_process_clusters(df, processes_df, both_io, only_in, only_out, max_cluster_pct=0.1, tolerance=1e-3):
    """
    Build clustering for processes: merges series chains and packs small
    processes into capped 'Others' buckets by level (source/middle/sink).

    Returns (process_to_cluster, clusters_info)
    where clusters_info maps cluster_id -> {name, type, members, throughput, level}.
    """
    metrics = compute_process_metrics(df)
    series_clusters = detect_series_clusters(df, metrics, tolerance=tolerance)

    proc_to_series = {}
    clusters_info = {}
    cluster_id_seq = 1
    proc_desc_map = processes_df.set_index('Process')['Description'].to_dict()

    for chain in series_clusters:
        cid = f"CL_SER_{cluster_id_seq:03d}"
        cluster_id_seq += 1
        for p in chain:
            proc_to_series[p] = cid
        names = [proc_desc_map.get(p, p) for p in chain]
        common = longest_common_substring(names)
        if common:
            cname = f"Series: {common.strip()}"
        elif len(names) <= 3:
            cname = f"Series: {' → '.join(names)}"
        else:
            cname = f"Series: {names[0]} → … → {names[-1]}"
        tp = metrics[metrics['process_code'].isin(chain)]
        thr = float(tp[['in_sum', 'out_sum']].max(axis=1).sum())
        clusters_info[cid] = {"name": cname, "type": "series", "members": chain, "throughput": thr, "level": "middle"}

    def throughput_of(pcode):
        row = metrics[metrics['process_code'] == pcode]
        if row.empty:
            return 0.0
        return float(max(row['in_sum'].iloc[0], row['out_sum'].iloc[0]))

    process_to_cluster = {}

    level_by_process = {}
    for p in only_out:
        level_by_process[p] = 'source'
    for p in only_in:
        level_by_process[p] = 'sink'
    for p in both_io:
        level_by_process[p] = 'middle'

    for p in both_io:
        process_to_cluster[p] = proc_to_series.get(p, p)

    dfx = df.copy()
    dfx['var_u'] = dfx['variable'].str.upper()
    sink_fin = dfx[(dfx['var_u'] == 'VAR_FIN') & (dfx['process_code'].isin(only_in))]
    total_final_energy = float(sink_fin['value'].sum())
    cap = max_cluster_pct * total_final_energy if total_final_energy > 0 else float('inf')

    def others_cluster_for_level(proc_list, level_name, cluster_code):
        nonlocal cluster_id_seq
        if not proc_list:
            return
        items = [(p, throughput_of(p)) for p in proc_list]
        if level_name == 'source':
            items = [(p, float(metrics.loc[metrics['process_code'] == p, 'out_sum'].iloc[0] if not metrics.loc[metrics['process_code'] == p].empty else 0.0)) for p, _ in items]
        elif level_name == 'sink':
            items = [(p, float(metrics.loc[metrics['process_code'] == p, 'in_sum'].iloc[0] if not metrics.loc[metrics['process_code'] == p].empty else 0.0)) for p, _ in items]
        else:
            items = [(p, throughput_of(p)) for p, _ in items]

        items.sort(key=lambda x: x[1], reverse=True)
        tail = []
        tail_sum = 0.0
        for p, v in reversed(items):
            if tail_sum + v <= cap:
                tail.append((p, v))
                tail_sum += v
            else:
                break
        if not tail:
            for p, _ in items:
                process_to_cluster[p] = proc_to_series.get(p, p) if level_name == 'middle' else p
            return

        tail_members = {p for p, _ in tail}
        for p, _ in items:
            if p in tail_members:
                continue
            process_to_cluster[p] = proc_to_series.get(p, p) if level_name == 'middle' else p

        cid = cluster_code
        members = sorted(list(tail_members))
        thr = float(tail_sum)
        names = [proc_desc_map.get(p, p) for p in members]
        common = longest_common_substring(names)
        cname = f"Others ({level_name})" + (f": {common.strip()}" if common else "")
        clusters_info[cid] = {"name": cname, "type": f"others_{level_name}", "members": members, "throughput": thr, "level": level_name}
        for p in members:
            process_to_cluster[p] = cid

    others_cluster_for_level(sorted(list(only_out)), 'source', 'CL_OTHERS_SRC')
    others_cluster_for_level(sorted(list(both_io)), 'middle', 'CL_OTHERS_MID')
    others_cluster_for_level(sorted(list(only_in)), 'sink', 'CL_OTHERS_SINK')

    return process_to_cluster, clusters_info


def apply_process_clustering(df, process_to_cluster, clusters_info, processes_df):
    """
    Replace process codes in DataFrame according to clustering and attach
    readable names for cluster nodes.
    """
    if df.empty:
        return df
    dfc = df.copy()
    dfc['process_code'] = dfc['process_code'].map(lambda p: process_to_cluster.get(p, p))
    proc_desc = processes_df.set_index('Process')['Description'].to_dict()
    name_map = {**{k: v for k, v in proc_desc.items()}, **{cid: info['name'] for cid, info in clusters_info.items()}}
    dfc['process'] = dfc['process_code'].map(lambda p: name_map.get(p, p))
    return dfc


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

    pypsa_col = get(['energy carrier - pypsa', 'pypsa', 'energy_carrier_pypsa'])
    times_col = get(['commodities times', 'commodities times ', 'times', 'commodity'])
    upst_comm_col = get(['uspstream_commodity', 'upstream_commodity'])
    upst_proc_col = get(['upstream_process'])
    sector_col = get(['sector (com_in)', 'sector'])
    comment_col = get(['comment', 'comments'])

    out = pd.DataFrame({
        'pypsa': df[pypsa_col] if pypsa_col in df else [],
        'times': df[times_col] if times_col in df else [],
        'upstream_commodity': df[upst_comm_col] if upst_comm_col in df else [],
        'upstream_process': df[upst_proc_col] if upst_proc_col in df else [],
        'sector': df[sector_col] if sector_col in df else [],
        'comment': df[comment_col] if comment_col in df else [],
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
        comm_desc = commodities_df.set_index('Commodity')['Description'].to_dict()
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

def _categorize_commodity(code, desc):
    """
    Fallback heuristic used ONLY when a TIMES commodity has no explicit
    mapping in data/mapping_commodities.csv.

    Maps raw TIMES commodity code/description to a small set of readable
    energy carrier categories to reduce Sankey node count.

    Returns a tuple (group_id, group_name).
    """
    c = (code or '').upper()
    d = (desc or '').upper()

    def has(*tokens):
        return any(t in c or t in d for t in tokens)

    # Handle ELC-prefixed commodities that actually denote fuels for electricity sector
    # e.g., ELCCOA, ELCGAS, ELCOIL, ELCNUC, ELCPEL vs ELCHIG/ELCMED/ELCLOW which are electricity
    code_u = (code or '').upper()
    if code_u.startswith('ELC'):
        # Electricity timeslice commodities
        if any(x in code_u for x in ['LOW', 'MED', 'HIG']):
            return 'COM_ELECTRICITY', 'Electricity'
        # Fuel-specific carriers for power sector
        if 'NUC' in code_u:
            return 'COM_NUCLEAR_FUEL', 'Nuclear Fuel'
        if 'COA' in code_u or 'COK' in code_u:
            return 'COM_COAL', 'Coal'
        if 'GAS' in code_u:
            return 'COM_NATURAL_GAS', 'Natural Gas'
        if 'OIL' in code_u or 'KER' in code_u or 'HFO' in code_u:
            return 'COM_OIL_PRODUCTS', 'Oil Products'
        if 'PEL' in code_u or 'BIO' in code_u:
            return 'COM_BIOMASS', 'Biomass & Biofuels'
        if any(x in code_u for x in ['RNW', 'SOL', 'WIN', 'HYD']):
            return 'COM_RENEWABLES', 'Renewables'
        # Default ELC* fall back to electricity
        return 'COM_ELECTRICITY', 'Electricity'

    # Nuclear fuel family (non-ELC prefixed)
    if has('NUC', 'NUCLEAR', 'URAN', 'URANIUM'):
        return 'COM_NUCLEAR_FUEL', 'Nuclear Fuel'

    # Gas family
    if has('GAS', 'NATURAL GAS'):
        return 'COM_NATURAL_GAS', 'Natural Gas'

    # Oil & refined products
    if has('OIL', 'GASOLINE', 'GSL', 'DIESEL', 'DSL', 'KER', 'HFO', 'PETROL'):
        return 'COM_OIL_PRODUCTS', 'Oil Products'

    # LPG distinct from general oil/gas where needed
    if has('LPG'):
        return 'COM_LPG', 'LPG'

    # Coal
    if has('COA', 'COAL', 'COK'):
        return 'COM_COAL', 'Coal'

    # Biomass / biofuels / pellets
    if has('BIO', 'PELLET', 'PEL', 'BIOFUEL', 'BIOMASS'):
        return 'COM_BIOMASS', 'Biomass & Biofuels'

    # Hydrogen
    if has('H2', 'HYDROGEN'):
        return 'COM_HYDROGEN', 'Hydrogen'

    # Heat / steam
    if has('HET', 'HEAT', 'STEAM'):
        return 'COM_HEAT', 'Heat/Steam'

    # Renewables as generic carrier if present as commodity (rare)
    if has('RNW', 'RENEW', 'SOL', 'WIN', 'WIND', 'HYD'):
        return 'COM_RENEWABLES', 'Renewables'

    return 'COM_OTHER', 'Other'


def build_commodity_groups(commodities_df, energy_commodity_codes):
    """
    Build grouping for commodities to reduce node count.

    Returns (commodity_to_group, groups_info) where:
    - commodity_to_group: dict original_code -> group_id
    - groups_info: dict group_id -> {name, members}
    """
    if commodities_df is None or commodities_df.empty:
        return {}, {}

    # Restrict to energy commodities
    subset = commodities_df[commodities_df['Commodity'].isin(energy_commodity_codes)].copy()
    if subset.empty:
        return {}, {}

    group_members = {}
    group_names = {}
    commodity_to_group = {}

    for _, row in subset.iterrows():
        code = row['Commodity']
        desc = row['Description']
        gid, gname = _categorize_commodity(code, desc)
        commodity_to_group[code] = gid
        group_names[gid] = gname
        group_members.setdefault(gid, []).append(code)

    groups_info = {
        gid: {
            'name': group_names.get(gid, gid),
            'type': 'commodity_group',
            'members': sorted(members)
        }
        for gid, members in group_members.items()
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


def build_sankey(df, output_html_file, year, flow_threshold=0.0):
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

    # Build tooltips that include source → target, commodity/cluster name and value
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
        if comm_label:
            tooltip = f"{src} → {tgt}<br>Commodity: {comm_label}<br>Value: {v:.2f} PJ"
        else:
            tooltip = f"{src} → {tgt}<br>Value: {v:.2f} PJ"
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

    # --- Simplification options ---
    cluster = False
    if cluster:
        # Set to False to build unclustered Sankey
        enable_process_clustering = True
        # Set to False to keep all commodity codes (no grouping)
        group_commodities = True
        # Max share of total final energy for the 'Others' buckets (0.10 = 10%)
        max_cluster_pct = 0.1
    else:
        # Set to False to build unclustered Sankey
        enable_process_clustering = False
        # Set to False to keep all commodity codes (no grouping)
        group_commodities = False
        # Max share of total final energy for the 'Others' buckets (0.10 = 10%)
        max_cluster_pct = 0  

    # --- Configuration ---
    vd_file = "data/bau_080925_0809.vd"
    selected_year = 2021
    commodities_file = "data/commodities.csv"
    processes_file = "data/processes.csv"
    output_csv_file = f"output/annual_values{'_clustered' if cluster else ''}.csv"
    output_filtered_csv = f"output/annual_flows_{selected_year}_energy{'_clustered' if cluster else ''}.csv"
    output_html_file = f"output/bau_sankey_{selected_year}_pj{'_clustered' if cluster else ''}.html"
  

    os.makedirs("output", exist_ok=True)

    # --- Load metadata ---
    print("Loading metadata...")
    commodities_map = parse_metadata_file(commodities_file)
    processes_map = parse_metadata_file(processes_file)
    commodities_df = pd.DataFrame(list(commodities_map.items()), columns=['Commodity', 'Description'])
    processes_df = pd.DataFrame(list(processes_map.items()), columns=['Process', 'Description'])

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
    filtered_df, energy_codes = filter_for_sankey(annual_values_df, commodities_df, year=selected_year)
    if filtered_df.empty:
        print("Filtered dataset for Sankey is empty; skipping Sankey generation.")
        return

    print(f"Writing filtered flows to {output_filtered_csv} ...")
    filtered_df.to_csv(output_filtered_csv, index=False)
    print("Done.")

    # --- Optional: Group commodities to reduce node count ---
    if group_commodities:
        mapping_file = "data/mapping_commodities.csv"
        mapping_df = read_commodity_mapping_table(mapping_file)
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
            filtered_df = apply_commodity_grouping(filtered_df, commodity_to_group, groups_info, commodities_df)

    # --- Analyze connectivity and warn isolated processes ---
    both_io, only_in, only_out = analyze_process_connectivity(filtered_df)

    # --- Option: process clustering toggle ---
    if not enable_process_clustering:
        print("Process clustering disabled. Building unclustered Sankey.")
        # Net internal bidirectional links to avoid loops even without clustering
        netted_df = net_bidirectional_links(filtered_df)
        _ = build_sankey(netted_df, output_html_file, year=selected_year, flow_threshold=0.0)
    else:
        # --- Build process clusters to simplify Sankey ---
        process_to_cluster, clusters_info = build_process_clusters(
            filtered_df, processes_df, both_io, only_in, only_out,
            max_cluster_pct=max_cluster_pct,
            tolerance=1e-3,
        )

        # Persist cluster definitions
        import json
        clusters_json_file = f"output/sankey_clusters_{selected_year}.json"
        clusters_csv_file = f"output/sankey_clusters_{selected_year}.csv"
        with open(clusters_json_file, 'w') as f:
            json.dump(clusters_info, f, indent=2)
        pd.DataFrame([
            {"cluster_id": cid, "name": info['name'], "type": info['type'], "throughput": info['throughput'], "members": ';'.join(info['members'])}
            for cid, info in clusters_info.items()
        ]).to_csv(clusters_csv_file, index=False)
        print(f"Saved clusters to {clusters_json_file} and {clusters_csv_file}")

        # Apply clustering to data
        clustered_df = apply_process_clustering(filtered_df, process_to_cluster, clusters_info, processes_df)
        # Net internal bidirectional links to avoid loops after clustering
        clustered_df = net_bidirectional_links(clustered_df)

        # --- Build Sankey ---
        _ = build_sankey(clustered_df, output_html_file, year=selected_year, flow_threshold=0.0)

if __name__ == "__main__":
    main()
