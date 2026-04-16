#!/usr/bin/env python3
"""
browse_arrhythmias.py — Download and visualize ECG samples from PhysioNet.

Opens a matplotlib window for each arrhythmia type showing candidate recordings.
Close each plot window to advance to the next one. Note down the record IDs you
like, then use explore_physionet.py --extract to add them to the simulator.

Usage:
  # See available arrhythmia codes
  python scripts/browse_arrhythmias.py --list

  # Browse atrial flutter and LBBB (2 samples each, Lead II)
  python scripts/browse_arrhythmias.py AF LBBB

  # More samples per type
  python scripts/browse_arrhythmias.py --count 5 AFIB

  # Show all 12 leads
  python scripts/browse_arrhythmias.py --all-leads AFIB

Requirements:
  pip install wfdb numpy matplotlib
"""

import argparse
import random
import re
import sys
import urllib.request

import numpy as np
import matplotlib.pyplot as plt
import wfdb

PHYSIONET_DB = 'ecg-arrhythmia/1.0.0'

LEAD_NAMES = ['I', 'II', 'III', 'aVR', 'aVL', 'aVF', 'V1', 'V2', 'V3', 'V4', 'V5', 'V6']

SNOMED_MAP = {
    'SR':    426783006, 'SB':    426177001, 'ST':    427084000,
    'SA':    427393009, 'AFIB':  164889003, 'AF':    164890007,
    'AT':    713422000, 'APB':   284470004, 'SVT':   426761007,
    'AVNRT': 233896004, 'AVRT': 233897008, 'VPB':   17338001,
    'VEB':   75532003, '1AVB':  270492004, '2AVB':  195042002,
    '3AVB':  27885002, 'LBBB':  164909002, 'RBBB':  59118001,
    'WPW':   74390002, 'LVH':   164873001, 'RVH':   89792004,
    'MI':    164865005,
}

FULL_NAMES = {
    'SR': 'Sinus Rhythm', 'SB': 'Sinus Bradycardia', 'ST': 'Sinus Tachycardia',
    'SA': 'Sinus Arrhythmia', 'AFIB': 'Atrial Fibrillation', 'AF': 'Atrial Flutter',
    'AT': 'Atrial Tachycardia', 'APB': 'Atrial Premature Beats',
    'SVT': 'Supraventricular Tachycardia', 'AVNRT': 'AV Node Reentrant Tachycardia',
    'AVRT': 'AV Reentrant Tachycardia', 'VPB': 'Ventricular Premature Beat',
    'VEB': 'Ventricular Escape Beat', '1AVB': '1st Degree AV Block',
    '2AVB': '2nd Degree AV Block', '3AVB': '3rd Degree AV Block (Complete)',
    'LBBB': 'Left Bundle Branch Block', 'RBBB': 'Right Bundle Branch Block',
    'WPW': 'Wolff-Parkinson-White', 'LVH': 'Left Ventricular Hypertrophy',
    'RVH': 'Right Ventricular Hypertrophy', 'MI': 'Myocardial Infarction',
}

SNOMED_REVERSE = {v: k for k, v in SNOMED_MAP.items()}


def list_records_in_directory(dir_path):
    """Scrape a PhysioNet directory listing for .hea file names."""
    url = f'https://physionet.org/files/{PHYSIONET_DB}/{dir_path}/'
    try:
        html = urllib.request.urlopen(url).read().decode()
        return re.findall(r'href="(JS\d+)\.hea"', html)
    except Exception:
        return []


def find_records(arrhythmia_code, count=2, max_dirs=50):
    """
    Find records matching an arrhythmia type.
    Scans directories randomly until enough matches are found.
    """
    target = SNOMED_MAP.get(arrhythmia_code)
    if not target:
        print(f"  Unknown code: {arrhythmia_code}")
        print(f"  Available: {', '.join(sorted(SNOMED_MAP.keys()))}")
        return []

    # Get list of directories
    directories = wfdb.get_record_list(PHYSIONET_DB)
    random.shuffle(directories)

    found = []  # list of (rec_name, dir_path)
    dirs_scanned = 0
    records_scanned = 0

    for dir_entry in directories[:max_dirs]:
        if len(found) >= count:
            break

        dir_path = dir_entry.strip('/')
        dirs_scanned += 1

        # List records in this directory
        rec_names = list_records_in_directory(dir_path)
        random.shuffle(rec_names)

        for rec_name in rec_names:
            if len(found) >= count:
                break
            records_scanned += 1
            try:
                hdr = wfdb.rdheader(rec_name, pn_dir=f'{PHYSIONET_DB}/{dir_path}')
                for comment in hdr.comments:
                    if comment.startswith('Dx:'):
                        codes = [int(c.strip()) for c in comment.replace('Dx:', '').split(',')]
                        if target in codes:
                            found.append((rec_name, dir_path))
                            labels = [SNOMED_REVERSE.get(c, str(c)) for c in codes]
                            print(f"  Found: {rec_name} in {dir_path} — Dx: {', '.join(labels)}")
                            break
            except Exception:
                continue

        if dirs_scanned % 10 == 0:
            print(f"  Scanned {dirs_scanned} dirs, {records_scanned} records, found {len(found)}...")

    print(f"  Total: scanned {dirs_scanned} dirs, {records_scanned} records, found {len(found)} matches.")
    return found


def load_record(rec_name, dir_path):
    """Download and return a wfdb record."""
    return wfdb.rdrecord(rec_name, pn_dir=f'{PHYSIONET_DB}/{dir_path}')


def plot_single_lead(records, arrhythmia_code, lead='II'):
    """Plot one lead from multiple records stacked vertically."""
    lead_idx = LEAD_NAMES.index(lead) if lead in LEAD_NAMES else 1
    n = len(records)

    fig, axes = plt.subplots(n, 1, figsize=(16, 3 * n), squeeze=False)
    fig.suptitle(f'{arrhythmia_code} — {FULL_NAMES.get(arrhythmia_code, "")} (Lead {lead})',
                 fontsize=16, fontweight='bold')

    for i, (rec_name, rec) in enumerate(records):
        ax = axes[i, 0]
        signal = rec.p_signal[:, lead_idx]
        t = np.arange(len(signal)) / rec.fs

        age = sex = ''
        dx_labels = []
        for comment in rec.comments:
            if comment.startswith('Age:'): age = comment
            if comment.startswith('Sex:'): sex = comment
            if comment.startswith('Dx:'):
                codes = [int(c.strip()) for c in comment.replace('Dx:', '').split(',')]
                dx_labels = [SNOMED_REVERSE.get(c, str(c)) for c in codes]

        ax.plot(t, signal, color='green', linewidth=0.8)
        ax.set_title(f'{rec_name}  |  {age}, {sex}  |  Dx: {", ".join(dx_labels)}',
                     fontsize=11, loc='left')
        ax.set_xlim(0, t[-1])
        ax.set_ylabel('mV')
        ax.grid(True, alpha=0.3)
        if i == n - 1:
            ax.set_xlabel('Time (s)')

    plt.tight_layout()
    plt.show()


def plot_all_leads(rec_name, rec, arrhythmia_code):
    """Plot all 12 leads for a single record."""
    signal = rec.p_signal
    t = np.arange(signal.shape[0]) / rec.fs

    fig, axes = plt.subplots(4, 3, figsize=(18, 12))
    fig.suptitle(f'{arrhythmia_code} — {rec_name}', fontsize=16, fontweight='bold')

    for i, (ax, name) in enumerate(zip(axes.flat, LEAD_NAMES)):
        ax.plot(t, signal[:, i], color='green', linewidth=0.8)
        ax.set_title(name, fontsize=12, fontweight='bold')
        ax.set_xlim(0, t[-1])
        ax.grid(True, alpha=0.3)
        if i >= 9:
            ax.set_xlabel('Time (s)')

    plt.tight_layout()
    plt.show()


def main():
    parser = argparse.ArgumentParser(
        description='Browse ECG arrhythmia samples from PhysioNet',
    )
    parser.add_argument('types', nargs='*', default=[],
                        help='Arrhythmia codes to browse (e.g. AF 3AVB)')
    parser.add_argument('--count', type=int, default=2,
                        help='Samples per type (default: 2)')
    parser.add_argument('--lead', type=str, default='II',
                        help='Lead to display (default: II)')
    parser.add_argument('--all-leads', action='store_true',
                        help='Show all 12 leads')
    parser.add_argument('--list', action='store_true',
                        help='List available codes')

    args = parser.parse_args()

    if args.list:
        print("Available codes:")
        for code, name in sorted(FULL_NAMES.items()):
            print(f"  {code:<8} {name}")
        return

    if not args.types:
        parser.print_help()
        return

    print(f"Searching for: {', '.join(args.types)} ({args.count} samples each)")
    print("This downloads headers from PhysioNet — may take 1-2 minutes per type.\n")

    for arrhythmia in args.types:
        print(f"\n{'='*60}")
        print(f"Searching for {arrhythmia} ({FULL_NAMES.get(arrhythmia, '?')})...")
        print(f"{'='*60}")

        matches = find_records(arrhythmia, count=args.count)
        if not matches:
            print(f"  No records found for {arrhythmia}")
            continue

        # Download signal data
        loaded = []
        for rec_name, dir_path in matches:
            try:
                print(f"  Downloading {rec_name}...")
                rec = load_record(rec_name, dir_path)
                loaded.append((rec_name, rec))
            except Exception as e:
                print(f"  Error loading {rec_name}: {e}")

        if not loaded:
            continue

        if args.all_leads:
            for rec_name, rec in loaded:
                plot_all_leads(rec_name, rec, arrhythmia)
        else:
            plot_single_lead(loaded, arrhythmia, lead=args.lead)

    print("\n\nDone! To extract a record you liked:")
    print("  python scripts/explore_physionet.py --extract <RECORD_ID> --label <CODE>")
    print("  python scripts/preprocess_signals.py")


if __name__ == '__main__':
    main()
