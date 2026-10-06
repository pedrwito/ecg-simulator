#!/usr/bin/env python3
"""
import_record.py — Append a 12-lead recording from any source to the raw CSVs.

explore_physionet.py --extract only knows the PhysioNet ECG-Arrhythmia
database. Recordings from other databases (PTB-XL, INCART, ...) arrive with
other sample rates and lengths, so this script takes a plain NumPy array and
brings it to what preprocess_signals.py expects: 500 Hz, 10 seconds, one CSV
row per lead.

Input: a .npy file of shape (n_leads, n_samples) in millivolts, leads in the
standard order I, II, III, aVR, aVL, aVF, V1..V6.

Usage:
  python scripts/import_record.py rec.npy --label VT --patient I47 --fs 257
  python scripts/preprocess_signals.py

Longer recordings are cut to the first 10 s (use --start to pick another
window); shorter ones are rejected rather than padded.

Requirements:
  pip install numpy scipy
"""

import argparse
import csv
import os
import sys

import numpy as np
import scipy.signal

LEAD_NAMES = ['I', 'II', 'III', 'aVR', 'aVL', 'aVF', 'V1', 'V2', 'V3', 'V4', 'V5', 'V6']
TARGET_FS = 500
SECONDS = 10


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[1])
    parser.add_argument('npy', help='Array of shape (n_leads, n_samples), in mV')
    parser.add_argument('--label', required=True, help='Rhythm key, e.g. VT')
    parser.add_argument('--patient', required=True, help='Record identifier')
    parser.add_argument('--fs', type=float, default=TARGET_FS, help='Sample rate of the array (default: 500)')
    parser.add_argument('--start', type=float, default=0, help='Start of the 10 s window, in seconds')
    parser.add_argument('--signals', default=os.path.join('old - original', 'signals.csv'))
    parser.add_argument('--labels', default=os.path.join('old - original', 'labels.csv'))
    args = parser.parse_args()

    data = np.load(args.npy).astype(float)
    if data.ndim != 2 or data.shape[0] != len(LEAD_NAMES):
        sys.exit(f"Expected shape (12, n_samples), got {data.shape}")
    if np.isnan(data).any():
        sys.exit("Recording contains NaN samples")

    with open(args.labels) as f:
        if any(row[:2] == [args.label, args.patient] for row in csv.reader(f)):
            sys.exit(f"{args.label}/{args.patient} is already in {args.labels}")

    first = int(round(args.start * args.fs))
    window = data[:, first:first + int(round(SECONDS * args.fs))]
    if window.shape[1] < int(SECONDS * args.fs) - 1:
        sys.exit(f"Need {SECONDS} s from {args.start} s, recording has {data.shape[1] / args.fs:.1f} s")

    n_out = TARGET_FS * SECONDS
    if window.shape[1] != n_out:
        window = scipy.signal.resample(window, n_out, axis=1)

    with open(args.signals, 'a', newline='') as sig_f, open(args.labels, 'a', newline='') as lbl_f:
        sig_writer = csv.writer(sig_f)
        lbl_writer = csv.writer(lbl_f)
        for lead_name, row in zip(LEAD_NAMES, window):
            sig_writer.writerow([f'{v:.6f}' for v in row])
            lbl_writer.writerow([args.label, args.patient, lead_name])

    print(f"Appended {args.label}/{args.patient}: 12 leads, {n_out} samples at {TARGET_FS} Hz")
    print("Next step: python scripts/preprocess_signals.py")


if __name__ == '__main__':
    main()
