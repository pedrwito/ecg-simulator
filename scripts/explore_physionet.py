#!/usr/bin/env python3
"""
explore_physionet.py — Browse, preview, and extract ECG signals from the
PhysioNet ECG-Arrhythmia database for use in the monitor simulator.

Workflow:
  1. Run with --list to see available arrhythmia types
  2. Run with --search <type> to find and preview candidate records
  3. Run with --extract to export selected records into the preprocessing pipeline

Examples:
  # See what arrhythmia types are available
  python scripts/explore_physionet.py --list

  # Search for atrial flutter records, preview 5 candidates (Lead II)
  python scripts/explore_physionet.py --search AF --count 5

  # Search and preview a specific lead
  python scripts/explore_physionet.py --search VT --count 3 --lead V1

  # Extract a specific record you liked from the preview
  python scripts/explore_physionet.py --extract JS00001 --label "Flutter Auricular"

  # After extracting, re-run the preprocessor to update signals.json:
  python scripts/preprocess_signals.py

Requirements:
  pip install wfdb numpy matplotlib
"""

import argparse
import csv
import os
import sys
import random

import numpy as np

# PhysioNet database path for wfdb
PHYSIONET_DB = 'ecg-arrhythmia/1.0.0'

# SNOMED codes for arrhythmia types we care about
# (from ConditionNames_SNOMED-CT.csv)
SNOMED_MAP = {
    'SR':     426783006,   # Sinus Rhythm
    'SB':     426177001,   # Sinus Bradycardia
    'ST':     427084000,   # Sinus Tachycardia
    'AFIB':   164889003,   # Atrial Fibrillation
    'AF':     164890007,   # Atrial Flutter
    'SVT':    426761007,   # Supraventricular Tachycardia
    'AT':     713422000,   # Atrial Tachycardia
    'VPB':    17338001,    # Ventricular Premature Beat
    'VEB':    75532003,    # Ventricular Escape Beat
    'LBBB':   164909002,   # Left Bundle Branch Block
    'RBBB':   59118001,    # Right Bundle Branch Block
    '1AVB':   270492004,   # 1st Degree AV Block
    '2AVB':   195042002,   # 2nd Degree AV Block
    '3AVB':   27885002,    # 3rd Degree AV Block
    'WPW':    74390002,    # Wolff-Parkinson-White
    'AVNRT':  233896004,   # AV Node Reentrant Tachycardia
    'AVRT':   233897008,   # AV Reentrant Tachycardia
    'APB':    284470004,   # Atrial Premature Beats
    'SA':     427393009,   # Sinus Irregularity / Sinus Arrhythmia
    'LVH':    164873001,   # Left Ventricular Hypertrophy
    'RVH':    89792004,    # Right Ventricular Hypertrophy
    'MI':     164865005,   # Myocardial Infarction
}

# Reverse map: SNOMED code → acronym
SNOMED_REVERSE = {v: k for k, v in SNOMED_MAP.items()}

# Lead names in the database
LEAD_NAMES = ['I', 'II', 'III', 'aVR', 'aVL', 'aVF', 'V1', 'V2', 'V3', 'V4', 'V5', 'V6']


def get_record_list():
    """Fetch the full record list from PhysioNet."""
    import wfdb
    # Download the RECORDS file
    records = wfdb.get_record_list(PHYSIONET_DB)
    return records


def get_record_diagnoses(record_path):
    """Read a record's header to get its SNOMED diagnosis codes."""
    import wfdb
    try:
        rec = wfdb.rdheader(record_path.split('/')[-1],
                            pn_dir=f'{PHYSIONET_DB}/{"/".join(record_path.split("/")[:-1])}')
        for comment in rec.comments:
            if comment.startswith('Dx:'):
                codes = [int(c.strip()) for c in comment.replace('Dx:', '').split(',')]
                return codes
    except Exception as e:
        print(f"  Warning: couldn't read {record_path}: {e}", file=sys.stderr)
    return []


def search_records(arrhythmia_code, max_candidates=20):
    """
    Search the PhysioNet database for records matching a given arrhythmia.
    Downloads headers (small) to check diagnoses — does NOT download signal data
    until you ask to preview a specific record.

    Returns list of (record_path, all_diagnosis_codes).
    """
    import wfdb
    target_snomed = SNOMED_MAP.get(arrhythmia_code)
    if not target_snomed:
        print(f"Unknown arrhythmia code: {arrhythmia_code}")
        print(f"Available: {', '.join(sorted(SNOMED_MAP.keys()))}")
        return []

    print(f"Searching for {arrhythmia_code} (SNOMED {target_snomed})...")
    print("Fetching record list...")
    records = get_record_list()
    print(f"Database has {len(records)} records total.")

    # Shuffle and scan — don't download all 45k headers
    random.shuffle(records)
    found = []
    scanned = 0

    print(f"Scanning headers (looking for {max_candidates} candidates)...")
    for rec_path in records:
        if len(found) >= max_candidates:
            break
        scanned += 1
        if scanned % 100 == 0:
            print(f"  Scanned {scanned} headers, found {len(found)} so far...")

        codes = get_record_diagnoses(rec_path)
        if target_snomed in codes:
            found.append((rec_path, codes))
            # Decode all diagnoses for display
            labels = [SNOMED_REVERSE.get(c, str(c)) for c in codes]
            print(f"  Found: {rec_path} — diagnoses: {', '.join(labels)}")

    print(f"\nDone. Scanned {scanned} headers, found {len(found)} matching records.")
    return found


def preview_record(record_path, lead='II', show_all_leads=False):
    """
    Download and plot a single record for visual inspection.
    """
    import wfdb
    import matplotlib.pyplot as plt

    parts = record_path.strip('/').split('/')
    rec_name = parts[-1]
    pn_dir = f'{PHYSIONET_DB}/{"/".join(parts[:-1])}'

    print(f"Downloading {record_path}...")
    rec = wfdb.rdrecord(rec_name, pn_dir=pn_dir)

    fs = rec.fs
    signal = rec.p_signal
    duration = signal.shape[0] / fs
    print(f"  {signal.shape[0]} samples, {fs}Hz, {duration:.1f}s, {signal.shape[1]} leads")
    print(f"  Comments: {rec.comments}")

    if show_all_leads:
        # Plot all 12 leads in a grid
        fig, axes = plt.subplots(4, 3, figsize=(18, 12))
        fig.suptitle(f'{record_path}', fontsize=14)
        t = np.arange(signal.shape[0]) / fs

        for i, (ax, name) in enumerate(zip(axes.flat, LEAD_NAMES)):
            ax.plot(t, signal[:, i], color='green', linewidth=0.8)
            ax.set_title(name, fontsize=12, fontweight='bold')
            ax.set_xlim(0, duration)
            ax.grid(True, alpha=0.3)
            if i >= 9:
                ax.set_xlabel('Time (s)')

        plt.tight_layout()
        plt.show()
    else:
        # Plot single lead
        lead_idx = LEAD_NAMES.index(lead) if lead in LEAD_NAMES else 1
        t = np.arange(signal.shape[0]) / fs

        fig, ax = plt.subplots(figsize=(14, 4))
        ax.plot(t, signal[:, lead_idx], color='green', linewidth=1)
        ax.set_title(f'{record_path} — Lead {lead}', fontsize=14)
        ax.set_xlabel('Time (s)')
        ax.set_ylabel('Amplitude (mV)')
        ax.set_xlim(0, duration)
        ax.grid(True, alpha=0.3)
        plt.tight_layout()
        plt.show()

    return rec


def extract_record(record_path, label, output_signals='old - original/signals.csv',
                   output_labels='old - original/labels.csv'):
    """
    Download a record and append all 12 leads to the existing signals/labels CSVs.
    After extracting, re-run preprocess_signals.py to update data/signals.json.
    """
    import wfdb

    parts = record_path.strip('/').split('/')
    rec_name = parts[-1]
    pn_dir = f'{PHYSIONET_DB}/{"/".join(parts[:-1])}'

    print(f"Downloading {record_path}...")
    rec = wfdb.rdrecord(rec_name, pn_dir=pn_dir)

    signal = rec.p_signal  # shape: (5000, 12)
    fs = rec.fs

    print(f"  {signal.shape[0]} samples at {fs}Hz, 12 leads")

    # Get the patient index from the record name (e.g. "JS00001" → "JS00001")
    patient_id = rec_name

    # Append each lead to CSVs
    leads_written = 0
    with open(output_signals, 'a', newline='') as sig_f, \
         open(output_labels, 'a', newline='') as lbl_f:
        sig_writer = csv.writer(sig_f)
        lbl_writer = csv.writer(lbl_f)

        for i, lead_name in enumerate(LEAD_NAMES):
            lead_signal = signal[:, i]
            sig_writer.writerow([f'{v:.6f}' for v in lead_signal])
            lbl_writer.writerow([label, patient_id, lead_name])
            leads_written += 1

    print(f"  Appended {leads_written} leads to {output_signals} and {output_labels}")
    print(f"  Label: '{label}', Patient: '{patient_id}'")
    print(f"\nNext step: run 'python scripts/preprocess_signals.py' to rebuild data/signals.json")


def list_arrhythmias():
    """Print all known arrhythmia types with their codes."""
    print("Available arrhythmia types:\n")
    print(f"  {'Code':<8} {'Name':<45} {'SNOMED CT'}")
    print(f"  {'─'*8} {'─'*45} {'─'*12}")

    # Group by category
    rhythms = ['SR', 'SB', 'ST', 'SA']
    atrial = ['AFIB', 'AF', 'AT', 'APB', 'SVT', 'AVNRT', 'AVRT']
    ventricular = ['VPB', 'VEB']
    blocks = ['1AVB', '2AVB', '3AVB', 'LBBB', 'RBBB']
    other = ['WPW', 'LVH', 'RVH', 'MI']

    for group_name, codes in [
        ('Sinus rhythms', rhythms),
        ('Atrial arrhythmias', atrial),
        ('Ventricular', ventricular),
        ('Conduction blocks', blocks),
        ('Other', other),
    ]:
        print(f"\n  {group_name}:")
        for code in codes:
            snomed = SNOMED_MAP[code]
            # Look up full name from reverse (we'll just use the code for now)
            names = {
                'SR': 'Sinus Rhythm', 'SB': 'Sinus Bradycardia',
                'ST': 'Sinus Tachycardia', 'SA': 'Sinus Arrhythmia',
                'AFIB': 'Atrial Fibrillation', 'AF': 'Atrial Flutter',
                'AT': 'Atrial Tachycardia', 'APB': 'Atrial Premature Beats',
                'SVT': 'Supraventricular Tachycardia',
                'AVNRT': 'AV Node Reentrant Tachycardia',
                'AVRT': 'AV Reentrant Tachycardia',
                'VPB': 'Ventricular Premature Beat',
                'VEB': 'Ventricular Escape Beat',
                '1AVB': '1st Degree AV Block', '2AVB': '2nd Degree AV Block',
                '3AVB': '3rd Degree AV Block (Complete)',
                'LBBB': 'Left Bundle Branch Block',
                'RBBB': 'Right Bundle Branch Block',
                'WPW': 'Wolff-Parkinson-White',
                'LVH': 'Left Ventricular Hypertrophy',
                'RVH': 'Right Ventricular Hypertrophy',
                'MI': 'Myocardial Infarction',
            }
            print(f"    {code:<8} {names.get(code, code):<45} {snomed}")


def main():
    parser = argparse.ArgumentParser(
        description='Browse and extract ECG signals from the PhysioNet ECG-Arrhythmia database.',
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  %(prog)s --list                              List available arrhythmia types
  %(prog)s --search AF --count 5               Find 5 atrial flutter records
  %(prog)s --search AF --count 3 --lead V1     Preview in lead V1
  %(prog)s --search AF --preview JS00001       Preview a specific record
  %(prog)s --search AF --preview JS00001 --all-leads   Preview all 12 leads
  %(prog)s --extract JS00001 --label AF        Extract record to CSVs

After extracting, rebuild signals.json:
  python scripts/preprocess_signals.py
        """
    )

    parser.add_argument('--list', action='store_true',
                        help='List all known arrhythmia types')
    parser.add_argument('--search', type=str, metavar='CODE',
                        help='Search for records matching this arrhythmia code (e.g. AF, VPB, 3AVB)')
    parser.add_argument('--count', type=int, default=5,
                        help='Number of candidate records to find (default: 5)')
    parser.add_argument('--lead', type=str, default='II',
                        help='Lead to preview (default: II)')
    parser.add_argument('--preview', type=str, metavar='RECORD',
                        help='Preview a specific record by name (e.g. JS00001)')
    parser.add_argument('--all-leads', action='store_true',
                        help='Show all 12 leads when previewing')
    parser.add_argument('--extract', type=str, metavar='RECORD',
                        help='Extract a record to the CSV files for preprocessing')
    parser.add_argument('--label', type=str,
                        help='Label for the extracted record (e.g. AF, 3AVB, VT)')

    args = parser.parse_args()

    if args.list:
        list_arrhythmias()
        return

    if args.search and not args.preview and not args.extract:
        # Search mode: find candidates and preview them one by one
        found = search_records(args.search, max_candidates=args.count)
        if found:
            print(f"\nPreviewing {len(found)} records (close each plot to see the next)...")
            for rec_path, codes in found:
                preview_record(rec_path, lead=args.lead, show_all_leads=args.all_leads)
        return

    if args.preview:
        # Need to find the full path for this record
        print(f"Looking up record {args.preview}...")
        records = get_record_list()
        matching = [r for r in records if args.preview in r]
        if not matching:
            print(f"Record {args.preview} not found in database.")
            return
        rec_path = matching[0]
        preview_record(rec_path, lead=args.lead, show_all_leads=args.all_leads)
        return

    if args.extract:
        if not args.label:
            print("Error: --label is required with --extract")
            print("Example: --extract JS00001 --label AF")
            return

        # Find full path
        print(f"Looking up record {args.extract}...")
        records = get_record_list()
        matching = [r for r in records if args.extract in r]
        if not matching:
            print(f"Record {args.extract} not found in database.")
            return
        rec_path = matching[0]
        extract_record(rec_path, args.label)
        return

    parser.print_help()


if __name__ == '__main__':
    main()
