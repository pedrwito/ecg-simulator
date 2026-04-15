#!/usr/bin/env python3
"""
preprocess_signals.py — Prepare raw ECG signals for the web-based monitor.

This script takes raw 500Hz ECG recordings and produces a ready-to-play JSON
file that the web app loads directly. No signal processing happens in the browser.

Pipeline per signal:
  1. Median filter baseline removal (removes baseline wander)
  2. Bandpass filter 0.5–50Hz (removes DC offset and high-frequency noise)
  3. Downsample 500Hz → 150Hz (matches the web app's virtual sample rate)
  4. Normalize amplitude to [-1, 1] range (consistent display scaling)
  5. Detect R-peaks using Pan-Tompkins algorithm
  6. Convert R-peak indices to 150Hz sample positions

Output format (data/signals.json):
  {
    "fs": 150,
    "rhythms": {
      "AFIB": {
        "patients": {
          "5": {
            "leads": {
              "II": {
                "signal": [0.01, -0.03, ...],   // float array, 3 decimal places
                "rPeaks": [45, 168, 293, ...]    // sample indices at 150Hz
              },
              "V1": { ... }
            }
          }
        }
      }
    }
  }

Usage:
  # From the project root:
  python scripts/preprocess_signals.py

  # With custom input/output paths:
  python scripts/preprocess_signals.py --signals path/to/signals.csv --labels path/to/labels.csv --output data/signals.json

  # To add new signals later, just update signals.csv and labels.csv with the
  # new rows and re-run this script. The output is fully regenerated each time.

Requirements:
  pip install numpy scipy
"""

import argparse
import csv
import json
import os
import sys

import numpy as np
import scipy.signal

# ─── Signal processing functions ────────────────────────────────────────────
# These replicate the pipeline from the original utils.py, used by the PyQt5
# prototype. Kept self-contained here so this script has no local imports.

INPUT_FS = 500    # Raw signal sample rate (Hz)
OUTPUT_FS = 150   # Target sample rate for the web app (Hz)


def median_filter_baseline(signal, fs):
    """
    Remove baseline wander using cascaded median filters.

    Two passes: 200ms window removes QRS-scale features, then 600ms window
    removes remaining P/T wave drift. Subtracting the 600ms median from the
    original signal yields a baseline-corrected result.

    This is the same as utils.py:med_filt().
    """
    kernel_200 = int(fs / 5 + 1)
    kernel_600 = int(3 * fs / 5 + 1)
    # Ensure odd kernel sizes (required by medfilt)
    if kernel_200 % 2 == 0:
        kernel_200 += 1
    if kernel_600 % 2 == 0:
        kernel_600 += 1
    med200 = scipy.signal.medfilt(signal, kernel_200)
    med600 = scipy.signal.medfilt(med200, kernel_600)
    return signal - med600


def bandpass_filter(signal, fs, lowcut=0.5, highcut=50):
    """
    5th-order Butterworth bandpass filter.

    0.5Hz highpass removes any remaining DC offset.
    50Hz lowpass removes powerline noise and high-frequency artifacts.
    filtfilt applies the filter forward and backward for zero phase distortion.

    This is the same as utils.py:pasabanda() with lowcut=0.5, highcut=50.
    """
    b, a = scipy.signal.butter(5, [lowcut, highcut], btype='band', fs=fs)
    return scipy.signal.filtfilt(b, a, signal)


def downsample(signal, original_fs, target_fs):
    """
    Downsample using scipy.signal.resample (polyphase / FFT-based).

    Preserves waveform morphology better than simple decimation because it
    applies an anti-aliasing filter internally.
    """
    duration_secs = len(signal) / original_fs
    target_samples = int(duration_secs * target_fs)
    return scipy.signal.resample(signal, target_samples)


def normalize(signal):
    """
    Normalize signal amplitude to [-1, 1] range.

    This ensures consistent Y-axis scaling in the web app regardless of the
    original recording's gain/units.
    """
    peak = max(abs(np.max(signal)), abs(np.min(signal)))
    if peak == 0:
        return signal
    return signal / peak


def detect_r_peaks(signal, fs):
    """
    Detect R-peaks using a simplified Pan-Tompkins approach.

    Pipeline: bandpass (8-25Hz) → derivative → square → integrate → find peaks.
    Then refine each peak position by finding the maximum absolute value in the
    original signal within a ±50ms window around each detected peak.

    This replicates utils.py:R_peaks() and utils.py:PanTompkins().
    """
    # Pan-Tompkins bandpass (8-25Hz — tighter than the display filter, optimized
    # for QRS detection)
    b, a = scipy.signal.butter(5, [8, 25], btype='band', fs=fs)
    filtered = scipy.signal.filtfilt(b, a, signal)

    # Derivative
    L = 1
    h = np.zeros(2 * L + 1)
    h[0] = 1
    h[-1] = -1
    h = h * fs / (2 * L)
    derived = np.convolve(filtered, h, 'same')

    # Square (rectify)
    squared = derived ** 2

    # Moving average integrator (150ms window)
    window_len = round(0.150 * fs)
    integrator = np.ones(window_len) / window_len
    integrated = np.convolve(squared, integrator, 'same')

    # Find peaks with minimum distance of 240ms (prevents double-detection)
    threshold = np.mean(integrated)
    peaks, _ = scipy.signal.find_peaks(
        integrated,
        height=threshold,
        distance=round(fs * 0.24)
    )

    # Refine peak positions: find max |signal| in ±50ms window around each peak
    k = int(0.05 * fs)
    refined = []
    for peak in peaks:
        start = max(0, peak - k)
        end = min(len(signal), start + 2 * k)
        window = np.abs(signal[start:end])
        refined.append(start + int(np.argmax(window)))

    return np.array(refined, dtype=int)


# ─── Main preprocessing ────────────────────────────────────────────────────

def process_signal(raw_signal):
    """
    Full preprocessing pipeline for one signal.

    Returns (processed_signal, r_peak_indices) both at OUTPUT_FS (150Hz).
    """
    signal = np.array(raw_signal, dtype=float)

    # Step 1-2: Baseline removal + bandpass at original sample rate
    # (filtering is more accurate at higher sample rates)
    signal = median_filter_baseline(signal, INPUT_FS)
    signal = bandpass_filter(signal, INPUT_FS, lowcut=0.5, highcut=50)

    # Step 3: Downsample to web app sample rate
    signal = downsample(signal, INPUT_FS, OUTPUT_FS)

    # Step 4: Normalize to [-1, 1]
    signal = normalize(signal)

    # Step 5: Detect R-peaks on the processed 150Hz signal
    r_peaks = detect_r_peaks(signal, OUTPUT_FS)

    return signal, r_peaks


def load_raw_data(signals_path, labels_path):
    """Load raw signals and labels from CSV files."""
    signals = []
    with open(signals_path, 'r') as f:
        for row in csv.reader(f):
            signals.append([float(v) for v in row])

    labels = []
    with open(labels_path, 'r') as f:
        for row in csv.reader(f):
            labels.append(row)

    if len(signals) != len(labels):
        print(f"WARNING: {len(signals)} signals but {len(labels)} labels", file=sys.stderr)

    return signals, labels


def build_output(signals, labels):
    """
    Process all signals and organize into the output JSON structure.

    Groups signals by rhythm → patient → lead for easy lookup in the web app.
    """
    output = {
        "fs": OUTPUT_FS,
        "rhythms": {}
    }

    total = len(signals)
    for i, (raw_signal, label) in enumerate(zip(signals, labels)):
        rhythm = label[0]       # e.g. "SR", "AFIB", "PACE", "SVTAC"
        patient = label[1]      # e.g. "5", "7", "15"
        lead = label[2]         # e.g. "I", "II", "V1"

        print(f"  [{i + 1}/{total}] {rhythm} patient {patient} lead {lead}...", end=" ")

        # Process
        processed, r_peaks = process_signal(raw_signal)

        # Round to 3 decimal places to reduce JSON size
        signal_list = [round(float(v), 3) for v in processed]
        r_peaks_list = [int(p) for p in r_peaks]

        print(f"{len(signal_list)} samples, {len(r_peaks_list)} R-peaks")

        # Build nested structure
        if rhythm not in output["rhythms"]:
            output["rhythms"][rhythm] = {"patients": {}}
        if patient not in output["rhythms"][rhythm]["patients"]:
            output["rhythms"][rhythm]["patients"][patient] = {"leads": {}}

        output["rhythms"][rhythm]["patients"][patient]["leads"][lead] = {
            "signal": signal_list,
            "rPeaks": r_peaks_list,
        }

    return output


def main():
    parser = argparse.ArgumentParser(
        description="Preprocess raw ECG signals for the web-based monitor simulator."
    )
    parser.add_argument(
        "--signals",
        default=os.path.join("old - original", "signals.csv"),
        help="Path to raw signals CSV (default: old - original/signals.csv)"
    )
    parser.add_argument(
        "--labels",
        default=os.path.join("old - original", "labels.csv"),
        help="Path to labels CSV (default: old - original/labels.csv)"
    )
    parser.add_argument(
        "--output",
        default=os.path.join("data", "signals.json"),
        help="Output JSON path (default: data/signals.json)"
    )
    args = parser.parse_args()

    print(f"Loading signals from: {args.signals}")
    print(f"Loading labels from:  {args.labels}")
    signals, labels = load_raw_data(args.signals, args.labels)
    print(f"Found {len(signals)} signals\n")

    print("Processing signals:")
    output = build_output(signals, labels)

    # Ensure output directory exists
    os.makedirs(os.path.dirname(args.output), exist_ok=True)

    print(f"\nWriting to: {args.output}")
    with open(args.output, 'w') as f:
        json.dump(output, f, separators=(',', ':'))  # compact JSON, no whitespace

    file_size = os.path.getsize(args.output)
    print(f"Done! Output size: {file_size / 1024:.0f} KB")

    # Summary
    print("\nSummary:")
    for rhythm, data in output["rhythms"].items():
        patients = list(data["patients"].keys())
        lead_count = sum(
            len(p["leads"]) for p in data["patients"].values()
        )
        print(f"  {rhythm}: {len(patients)} patient(s), {lead_count} signals")


if __name__ == "__main__":
    main()
