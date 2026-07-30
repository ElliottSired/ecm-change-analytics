"""
Generates ECM_Change_Management's synthetic source data and writes it to data/bronze/.

This is the one-time (or re-run-when-you-want-a-new-scenario) data generation step.
The notebook (notebooks/EPC_Change_Management_Analysis.ipynb) no longer generates
data inline — it reads the CSVs this script produces, the same way a production
pipeline would read an upstream ECM system extract. Re-run this script any time you
want to regenerate the scenario (same RANDOM_SEED = identical output every time).

Usage:
    python3 src/generate_source_data.py
"""
import json
import random
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parent.parent
SRC = ROOT / "data" / "bronze"

RANDOM_SEED = 42

# ── MASTER CONFIGURATION ────────────────────────────────────────────────────
SIM_TODAY = date(2026, 2, 2)
ts_sim_today = pd.Timestamp(SIM_TODAY)
PROJECT_END = date(2027, 12, 31)
NUM_CHANGES = 15000

MILESTONE_LIST = ['M1', 'M2', 'M3', 'M4']

# Bias toward M2/M3/M4 so the assumed view looks spread and healthy.
# Many of those systems belong to M1/M2 in systems_master → driving view shifts
# a large population to earlier deadlines → unhealthy when viewed by driving milestone.
MILESTONE_PROBS = [0.20, 0.30, 0.30, 0.20]

# L1 = complex, high-impact, slow to resolve
# L2 = moderate
# L3 = simple, fast to close
LEVEL_DURATION = {1: 450, 2: 180, 3: 60}
LEVEL_PROBS_DEFAULT = [0.05, 0.25, 0.70]
LEVEL_PROBS_DOC = [0.01, 0.10, 0.89]

CONTRACT_SYSTEM_MAP = {
    'Electrical':      ['ABC', 'ABD', 'ABB', 'DEF'],
    'Mechanical':      ['GHP', 'BSG', 'XYZ', 'LMN'],
    'Civil':           ['PQR', 'STU', 'VWX', 'YZA'],
    'I&C':             ['CDE', 'FGH', 'IJK', 'LMO'],
    'Instrumentation': ['KLM', 'NOP', 'QRS', 'TUV'],
    'Piping':          ['WXY', 'ZAB', 'XYA', 'XYB', 'DEG', 'DEH'],
}
ALL_SYSTEMS = [s for systems in CONTRACT_SYSTEM_MAP.values() for s in systems]
SYSTEM_TO_CONTRACT = {
    s: contract
    for contract, systems in CONTRACT_SYSTEM_MAP.items()
    for s in systems
}

# The only performance bias in the model.
# Positive = slower system → DateClosed pushed later → open changes naturally
#   older because they opened at the same time but haven't closed yet.
# Negative = faster system → DateClosed earlier → lower lead time, fewer open.
SYSTEM_CLOSURE_DELAY = {
    'ABB': 320, 'XYZ': 290, 'DEG': 260, 'LMN': 220, 'CDE': 190, 'BSG': 140,
    'ABC': -60, 'PQR': -55, 'FGH': -50, 'KLM': -40,
}

# Shifts DateClosed earlier for poor systems so their closures fall well
# outside the 90-day lookback window used by Current_Rate_Weekly downstream.
SYSTEM_RECENT_CLOSE_BIAS = {
    'ABB': 120, 'XYZ': 110, 'DEG': 100, 'LMN': 85, 'CDE': 75, 'BSG': 60,
}

POTENTIAL_CONSEQUENCES = [
    'Documentary Only', 'Drawing Update Only', 'Specification Clarification',
    'Calculation Revision Only', 'Procedure Update Only', 'Cost Impact',
    'Schedule Impact', 'Design Impact', 'Safety Impact', 'Material Change Required',
    'Construction Rework Required', 'Equipment Replacement Required',
    'Installation Modification Required', 'Multi-Discipline Impact',
]
CONSEQUENCE_PROBS = np.array([
    0.50, 0.05, 0.04, 0.07, 0.03,
    0.05, 0.06, 0.04, 0.03, 0.03,
    0.02, 0.04, 0.02, 0.01,
])
CONSEQUENCE_PROBS /= CONSEQUENCE_PROBS.sum()

DOCUMENT_ONLY = [
    'Documentary Only', 'Drawing Update Only', 'Specification Clarification',
    'Calculation Revision Only', 'Procedure Update Only',
]

CHANGE_REASONS = [
    'Design Optimization', 'Client Requirement Change', 'Site Condition Variation',
    'Vendor Deviation', 'Constructability Improvement', 'Late Engineering Input',
    'Regulatory Compliance Update', 'Safety Enhancement', 'Clash Detection Resolution',
    'Interface Misalignment', 'Material Availability Issue', 'Procurement Specification Change',
    'Calculation Revision', 'Drawing Update', 'Scope Clarification',
    'Value Engineering Proposal', 'Risk Mitigation Measure', 'Quality Non-Conformance',
]
REASON_PROBS = np.array([
    0.12, 0.10, 0.08, 0.07, 0.07, 0.06,
    0.05, 0.05, 0.05, 0.05, 0.05, 0.05,
    0.04, 0.06, 0.05, 0.04, 0.03, 0.03,
])
REASON_PROBS /= REASON_PROBS.sum()


def get_open_date(milestone):
    """Spread DateOpened across the realistic project year for each milestone."""
    year = {'M1': 2023, 'M2': 2023, 'M3': 2024, 'M4': 2025}.get(milestone, 2024)
    return date(year, 1, 1) + timedelta(days=random.randint(0, 364))


def format_systems(systems):
    if not systems:
        return ''
    groups = defaultdict(list)
    for s in systems:
        groups[s[:2]].append(s)
    parts = []
    for prefix, members in groups.items():
        if len(members) > 1:
            base = members[0]
            suffixes = '/'.join([m[2:] for m in members[1:]])
            parts.append(f'{base}/{suffixes}')
        else:
            parts.append(members[0])
    return ', '.join(parts)


def build_fact_table():
    """The change-request fact table — one row per ChangeID, as if pulled from the
    ECM system's API. Deterministic given RANDOM_SEED (set by the caller)."""
    records = []
    for i in range(1, NUM_CHANGES + 1):
        change_id = f'C{i:05d}'
        assumed_m = np.random.choice(MILESTONE_LIST, p=MILESTONE_PROBS)
        date_opened = pd.Timestamp(get_open_date(assumed_m))

        consequence = np.random.choice(POTENTIAL_CONSEQUENCES, p=CONSEQUENCE_PROBS)
        is_doc_only = consequence in DOCUMENT_ONLY

        if is_doc_only:
            level = int(np.random.choice([1, 2, 3], p=LEVEL_PROBS_DOC))
            base_duration = LEVEL_DURATION[level] * 0.7
        else:
            level = int(np.random.choice([1, 2, 3], p=LEVEL_PROBS_DEFAULT))
            base_duration = LEVEL_DURATION[level]

        num_to_pick = np.random.choice([1, 2, 3, 4], p=[0.45, 0.35, 0.15, 0.05])
        selected_systems = random.sample(ALL_SYSTEMS, k=num_to_pick)
        systems_string = format_systems(selected_systems)
        primary_system = selected_systems[0]

        system_penalty = SYSTEM_CLOSURE_DELAY.get(primary_system, 0)
        target_closure = date_opened + timedelta(days=int(base_duration))
        raw_closed_date = target_closure + timedelta(
            days=random.randint(-20, 30) + system_penalty
        )

        if is_doc_only:
            raw_closed_date -= timedelta(days=random.randint(15, 45))

        if raw_closed_date <= date_opened:
            raw_closed_date = date_opened + timedelta(days=random.randint(2, 7))

        if raw_closed_date <= pd.Timestamp(ts_sim_today):
            status = 'Closed'
            recent_bias = SYSTEM_RECENT_CLOSE_BIAS.get(primary_system, 0)
            biased_close = pd.Timestamp(raw_closed_date) - timedelta(days=recent_bias)
            if biased_close <= date_opened:
                biased_close = date_opened + timedelta(days=random.randint(2, 7))
            date_closed = biased_close
        else:
            status = 'Validated' if random.random() > 0.15 else 'Open'
            date_closed = None

        if status in ['Closed', 'Validated']:
            upper_limit = date_closed if status == 'Closed' else ts_sim_today
            diff = (upper_limit - date_opened).days
            date_validated = date_opened + timedelta(
                days=random.randint(1, max(1, min(30, diff - 1)))
            )
        else:
            date_validated = None

        records.append({
            'ChangeID':              change_id,
            'AssumedMilestone':      assumed_m,
            'Level':                 level,
            'DateOpened':            date_opened,
            'DateValidated':         date_validated,
            'DateClosed':            date_closed,
            'TargetClosureDate':     pd.Timestamp(target_closure),
            'Status':                status,
            'SystemsImpacted':       systems_string,
            'Reason':                np.random.choice(CHANGE_REASONS, p=REASON_PROBS),
            'PotentialConsequences': consequence,
        })

    return pd.DataFrame(records)


def build_contract_dim():
    """One row per system — each system belongs to exactly one contract. A seed
    table: static, manually defined, never derived from fact data."""
    return pd.DataFrame([
        {'SystemCode': sys, 'Contract': contract}
        for contract, systems in CONTRACT_SYSTEM_MAP.items()
        for sys in systems
    ])


def build_milestone_dim():
    """One row per milestone. Project_Target_Date is the commissioning deadline
    for each milestone."""
    return pd.DataFrame({
        'Milestone_ID':        ['M1', 'M2', 'M3', 'M4'],
        'Milestone_Name':      ['System Installation', 'Initial Testing', 'Verification', 'Handover'],
        'Project_Target_Date': pd.to_datetime(['2026-01-15', '2026-06-15', '2026-12-30', '2027-04-20']),
    })


def build_systems_master():
    """One row per system — maps each system to its commissioning milestone."""
    system_mapping = {
        'M1': ['PQR', 'STU', 'VWX', 'YZA', 'WXY', 'ZAB'],
        'M2': ['KLM', 'NOP', 'QRS', 'TUV'],
        'M3': ['ABC', 'ABD', 'ABB', 'DEF', 'DEG', 'DEH', 'GHP', 'BSG', 'XYZ', 'LMN', 'XYA', 'XYB'],
        'M4': ['CDE', 'FGH', 'IJK', 'LMO'],
    }
    return pd.DataFrame([
        {'SystemCode': system, 'Milestone_ID': milestone}
        for milestone, systems in system_mapping.items()
        for system in systems
    ])


def build_date_dim():
    """Full date spine 2021-2027 covering the project lifecycle."""
    df_date = pd.DataFrame({'Date': pd.date_range('2021-01-01', '2027-12-31')})
    df_date['Date_Key'] = df_date['Date'].dt.strftime('%Y-%m-%d')
    df_date['Year'] = df_date['Date'].dt.year
    df_date['Month_Name'] = df_date['Date'].dt.month_name()
    return df_date


def build_building_dim():
    """10 buildings covering the project site."""
    buildings = [
        {'Building_ID': 'B01', 'Building_Name': 'Reactor Building',       'Building_Type': 'Nuclear'},
        {'Building_ID': 'B02', 'Building_Name': 'Turbine Hall',           'Building_Type': 'Generation'},
        {'Building_ID': 'B03', 'Building_Name': 'Control Building',       'Building_Type': 'Control'},
        {'Building_ID': 'B04', 'Building_Name': 'Electrical Building',    'Building_Type': 'Electrical'},
        {'Building_ID': 'B05', 'Building_Name': 'Auxiliary Building',     'Building_Type': 'Auxiliary'},
        {'Building_ID': 'B06', 'Building_Name': 'Diesel Generator House', 'Building_Type': 'Auxiliary'},
        {'Building_ID': 'B07', 'Building_Name': 'Water Treatment Plant',  'Building_Type': 'Civil'},
        {'Building_ID': 'B08', 'Building_Name': 'Cooling Tower',          'Building_Type': 'Mechanical'},
        {'Building_ID': 'B09', 'Building_Name': 'Waste Building',         'Building_Type': 'Civil'},
        {'Building_ID': 'B10', 'Building_Name': 'Administration Block',   'Building_Type': 'Admin'},
    ]
    return pd.DataFrame(buildings)


def build_zone_dim():
    """Each zone belongs to a building. Not every building has named zones —
    whole-building systems use a NULL zone (handled in system_location_bridge)."""
    zones = [
        {'Zone_ID': 'B01-Z1', 'Building_ID': 'B01', 'Zone_Name': 'Containment',         'Zone_Type': 'Restricted'},
        {'Zone_ID': 'B01-Z2', 'Building_ID': 'B01', 'Zone_Name': 'Primary Circuit',      'Zone_Type': 'Restricted'},
        {'Zone_ID': 'B01-Z3', 'Building_ID': 'B01', 'Zone_Name': 'Auxiliary Systems',    'Zone_Type': 'Controlled'},
        {'Zone_ID': 'B02-Z1', 'Building_ID': 'B02', 'Zone_Name': 'Turbine Deck',         'Zone_Type': 'Operational'},
        {'Zone_ID': 'B02-Z2', 'Building_ID': 'B02', 'Zone_Name': 'Condenser Area',       'Zone_Type': 'Operational'},
        {'Zone_ID': 'B02-Z3', 'Building_ID': 'B02', 'Zone_Name': 'Feed Water Area',      'Zone_Type': 'Operational'},
        {'Zone_ID': 'B03-Z1', 'Building_ID': 'B03', 'Zone_Name': 'Main Control Room',    'Zone_Type': 'Critical'},
        {'Zone_ID': 'B03-Z2', 'Building_ID': 'B03', 'Zone_Name': 'Cable Spreading Room', 'Zone_Type': 'Controlled'},
        {'Zone_ID': 'B03-Z3', 'Building_ID': 'B03', 'Zone_Name': 'Battery Room',         'Zone_Type': 'Controlled'},
        {'Zone_ID': 'B04-Z1', 'Building_ID': 'B04', 'Zone_Name': 'Switchgear Room',      'Zone_Type': 'Controlled'},
        {'Zone_ID': 'B04-Z2', 'Building_ID': 'B04', 'Zone_Name': 'MCC Room',             'Zone_Type': 'Controlled'},
        {'Zone_ID': 'B05-Z1', 'Building_ID': 'B05', 'Zone_Name': 'HVAC Plant Room',      'Zone_Type': 'Operational'},
        {'Zone_ID': 'B05-Z2', 'Building_ID': 'B05', 'Zone_Name': 'Pipe Chase North',     'Zone_Type': 'Operational'},
        {'Zone_ID': 'B05-Z3', 'Building_ID': 'B05', 'Zone_Name': 'Pipe Chase South',     'Zone_Type': 'Operational'},
        {'Zone_ID': 'B07-Z1', 'Building_ID': 'B07', 'Zone_Name': 'Chemical Dosing',      'Zone_Type': 'Operational'},
        {'Zone_ID': 'B07-Z2', 'Building_ID': 'B07', 'Zone_Name': 'Filter Hall',          'Zone_Type': 'Operational'},
    ]
    return pd.DataFrame(zones)


def build_system_location_bridge():
    """Maps SystemCode -> Building -> Zone (Zone_ID = None means whole-building
    scope). A system can appear multiple times if it spans buildings or zones."""
    system_locations = [
        {'SystemCode': 'ABC', 'Building_ID': 'B04', 'Zone_ID': 'B04-Z1'},
        {'SystemCode': 'ABC', 'Building_ID': 'B04', 'Zone_ID': 'B04-Z2'},
        {'SystemCode': 'ABD', 'Building_ID': 'B04', 'Zone_ID': 'B04-Z1'},
        {'SystemCode': 'ABB', 'Building_ID': 'B03', 'Zone_ID': 'B03-Z3'},
        {'SystemCode': 'ABB', 'Building_ID': 'B06', 'Zone_ID': None},
        {'SystemCode': 'DEF', 'Building_ID': 'B04', 'Zone_ID': None},
        {'SystemCode': 'GHP', 'Building_ID': 'B02', 'Zone_ID': 'B02-Z1'},
        {'SystemCode': 'GHP', 'Building_ID': 'B02', 'Zone_ID': 'B02-Z2'},
        {'SystemCode': 'BSG', 'Building_ID': 'B02', 'Zone_ID': 'B02-Z3'},
        {'SystemCode': 'XYZ', 'Building_ID': 'B08', 'Zone_ID': None},
        {'SystemCode': 'LMN', 'Building_ID': 'B05', 'Zone_ID': 'B05-Z1'},
        {'SystemCode': 'LMN', 'Building_ID': 'B01', 'Zone_ID': 'B01-Z3'},
        {'SystemCode': 'PQR', 'Building_ID': 'B07', 'Zone_ID': None},
        {'SystemCode': 'STU', 'Building_ID': 'B09', 'Zone_ID': None},
        {'SystemCode': 'VWX', 'Building_ID': 'B01', 'Zone_ID': 'B01-Z1'},
        {'SystemCode': 'YZA', 'Building_ID': 'B01', 'Zone_ID': 'B01-Z2'},
        {'SystemCode': 'CDE', 'Building_ID': 'B03', 'Zone_ID': 'B03-Z1'},
        {'SystemCode': 'CDE', 'Building_ID': 'B03', 'Zone_ID': 'B03-Z2'},
        {'SystemCode': 'FGH', 'Building_ID': 'B03', 'Zone_ID': 'B03-Z1'},
        {'SystemCode': 'IJK', 'Building_ID': 'B03', 'Zone_ID': 'B03-Z2'},
        {'SystemCode': 'LMO', 'Building_ID': 'B04', 'Zone_ID': 'B04-Z1'},
        {'SystemCode': 'KLM', 'Building_ID': 'B01', 'Zone_ID': 'B01-Z1'},
        {'SystemCode': 'KLM', 'Building_ID': 'B01', 'Zone_ID': 'B01-Z2'},
        {'SystemCode': 'NOP', 'Building_ID': 'B02', 'Zone_ID': 'B02-Z1'},
        {'SystemCode': 'QRS', 'Building_ID': 'B05', 'Zone_ID': 'B05-Z1'},
        {'SystemCode': 'TUV', 'Building_ID': 'B07', 'Zone_ID': 'B07-Z1'},
        {'SystemCode': 'TUV', 'Building_ID': 'B07', 'Zone_ID': 'B07-Z2'},
        {'SystemCode': 'WXY', 'Building_ID': 'B05', 'Zone_ID': 'B05-Z2'},
        {'SystemCode': 'WXY', 'Building_ID': 'B05', 'Zone_ID': 'B05-Z3'},
        {'SystemCode': 'ZAB', 'Building_ID': 'B01', 'Zone_ID': 'B01-Z3'},
        {'SystemCode': 'XYA', 'Building_ID': 'B02', 'Zone_ID': 'B02-Z2'},
        {'SystemCode': 'XYA', 'Building_ID': 'B08', 'Zone_ID': None},
        {'SystemCode': 'XYB', 'Building_ID': 'B02', 'Zone_ID': 'B02-Z3'},
        {'SystemCode': 'DEG', 'Building_ID': 'B05', 'Zone_ID': 'B05-Z2'},
        {'SystemCode': 'DEG', 'Building_ID': 'B05', 'Zone_ID': 'B05-Z3'},
        {'SystemCode': 'DEH', 'Building_ID': 'B07', 'Zone_ID': 'B07-Z2'},
    ]
    return pd.DataFrame(system_locations)


def main():
    random.seed(RANDOM_SEED)
    np.random.seed(RANDOM_SEED)

    SRC.mkdir(parents=True, exist_ok=True)

    tables = {
        "fact_table":              build_fact_table(),
        "contract_dim":            build_contract_dim(),
        "commissioning_milestones": build_milestone_dim(),
        "systems_master":          build_systems_master(),
        "date_dim":                build_date_dim(),
        "building_dim":            build_building_dim(),
        "zone_dim":                build_zone_dim(),
        "system_location_bridge":  build_system_location_bridge(),
    }
    for name, df in tables.items():
        df.to_csv(SRC / f"{name}.csv", index=False)
        manifest = {
            "table": name,
            "layer": "bronze",
            "built_from": [],
            "built_at": datetime.now(timezone.utc).isoformat(),
            "rows": len(df),
            "columns": list(df.columns),
        }
        (SRC / f"{name}.json").write_text(json.dumps(manifest, indent=2))
        print(f"wrote {name:26s} {len(df):>6,} rows -> {SRC / f'{name}.csv'}")


if __name__ == "__main__":
    main()
