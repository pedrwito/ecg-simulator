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
  5. Trim to a seamless loop (same cut for every lead of a patient)
  6. Detect R-peaks using Pan-Tompkins algorithm on the trimmed signal

Output format (data/signals.json):
  {
    "fs": 150,
    "rhythms": {
      "AFIB": {
        "patients": {
          "5": {
            "leads": {
            "nLeads": 12,                        // distinct lead signals (1 = one
                                                 // lead copied into all 12 slots)
            "leads": {
              "II": {
                "signal": [0.01, -0.03, ...],   // float array, 3 decimal places
                "rPeaks": [45, 168, 293, ...],   // sample indices at 150Hz
                "mv": 1.234                      // mV at signal == 1.0 (omitted if
                                                 // the source was pre-normalized)
              },
              "V1": { ... }
            }
          }
        }
      }
    }
  }

Signals are a little under 10 s and differ in length between patients: the
web app plays them on repeat, and step 5 trims each one so that its end runs
into its start between two beats instead of jumping mid-complex.

The monitor only needs the normalized signal. The electrocardiograph view
(js/ecg.js) draws on millimetre paper at 10 mm/mV, so it also needs `mv` to
restore each lead's true amplitude, and `nLeads` to know whether a 12-lead
layout would be honest.

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
from scipy.spatial.distance import cdist

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

    Returns (processed_signal, peak) — the signal at OUTPUT_FS (150Hz) and the
    amplitude (in source units) that was scaled to 1.0 by the normalization
    step. R-peaks are detected later, once the loop has been trimmed.
    """
    signal = np.array(raw_signal, dtype=float)

    # Step 1-2: Baseline removal + bandpass at original sample rate
    # (filtering is more accurate at higher sample rates)
    signal = median_filter_baseline(signal, INPUT_FS)
    signal = bandpass_filter(signal, INPUT_FS, lowcut=0.5, highcut=50)

    # Step 3: Downsample to web app sample rate
    signal = downsample(signal, INPUT_FS, OUTPUT_FS)

    # Step 4: Normalize to [-1, 1], remembering the scale that was divided out
    peak = float(max(abs(np.max(signal)), abs(np.min(signal))))
    signal = normalize(signal)

    return signal, peak


# ─── Seamless looping ───────────────────────────────────────────────────────
# The web app repeats each recording for as long as the rhythm is on screen.
# Played as-is, the last sample is followed by the first, which lands anywhere
# in the cardiac cycle: a visible jump, and often a beat that arrives too early
# or too late — an arrhythmia that is not in the recording. Trimming the
# recording to a well-chosen [start, end) removes both.

LOOP_MATCH = round(0.10 * OUTPUT_FS)   # samples compared each side of a cut point
LOOP_FADE = round(0.10 * OUTPUT_FS)    # crossfade that absorbs the last mismatch
LOOP_TOLERANCE = 0.12                  # mismatch the crossfade hides, as a fraction of the
                                       # signal's own RMS around the cut (floor: 0.03 of full scale)


def beat_positions(leads, fs):
    """
    QRS positions common to all leads of one patient.

    Uses the slope energy summed across leads rather than a single lead, so a
    lead where the QRS happens to be small cannot hide a beat.
    """
    energy = np.abs(np.gradient(leads, axis=1)).sum(axis=0)
    window = round(0.06 * fs)
    energy = np.convolve(energy, np.ones(window) / window, 'same')
    peaks, _ = scipy.signal.find_peaks(
        energy, height=0.35 * np.percentile(energy, 99), distance=round(0.25 * fs)
    )
    return peaks


def find_loop(leads, fs):
    """
    Choose [start, end) so that the recording loops without a visible seam.

    Two conditions, in order:
      1. The beat-to-beat interval across the seam must look like the others.
         For a regular rhythm that means within 3% of the median R-R; for an
         irregular one (AFib, ectopy, VFib), within the range the recording
         already shows. This is what stops the loop from inventing a pause or
         a premature beat.
      2. The waveform around `end` must match the waveform around `start` in
         every lead, so the join is smooth.
    Any cut that matches to within LOOP_TOLERANCE is as good as invisible once
    crossfaded, so among those the longest loop wins: the best-matching pair
    of points is often only a few seconds apart, and throwing away half of a
    10 s recording to shave an imperceptible mismatch is a bad trade.

    @param leads: array (n_leads, n_samples), all leads of one patient
    @returns (start, end) sample indices
    """
    n = leads.shape[1]
    starts = np.arange(LOOP_MATCH + LOOP_FADE, int(n * 0.3))
    ends = np.arange(int(n * 0.7), n - LOOP_MATCH)

    def windows(points):
        # Shape only: each lead's local mean is removed, so slow baseline drift
        # between the start and the end of the recording does not rule out an
        # otherwise perfect cut. The crossfade turns that offset into a gentle
        # ramp instead of a step.
        out = []
        for p in points:
            w = leads[:, p - LOOP_MATCH:p + LOOP_MATCH]
            out.append((w - w.mean(axis=1, keepdims=True)).ravel())
        return np.array(out)

    cost = cdist(windows(starts), windows(ends), 'sqeuclidean')

    beats = beat_positions(leads, fs)
    if len(beats) >= 4:
        rr = np.diff(beats)
        if rr.std() / rr.mean() < 0.06:
            low, high = np.median(rr) * 0.97, np.median(rr) * 1.03
        else:
            low, high = np.percentile(rr, 10), np.percentile(rr, 90)

        # R-R across the seam = (end → previous beat) + (start → next beat)
        after_start = np.array([beats[beats > p][0] - p if (beats > p).any() else np.nan for p in starts])
        before_end = np.array([p - beats[beats < p][-1] if (beats < p).any() else np.nan for p in ends])
        seam_rr = after_start[:, None] + before_end[None, :]
        allowed = (seam_rr >= low) & (seam_rr <= high)
        if allowed.any():
            cost = np.where(allowed, cost, np.inf)

    # Tolerance scales with how busy the signal is: a mismatch that would show
    # on a quiet baseline is lost in the swings of VT or VFib.
    rms = np.sqrt(cost / (leads.shape[0] * 2 * LOOP_MATCH))
    busy = np.median(np.sqrt((windows(ends) ** 2).mean(axis=1)))
    good = np.argwhere(rms <= max(0.03, LOOP_TOLERANCE * busy, rms.min() * 1.2))
    i, j = max(good, key=lambda ij: ends[ij[1]] - starts[ij[0]])
    return int(starts[i]), int(ends[j])


def make_loop(leads, start, end):
    """
    Cut [start, end) and crossfade its tail into the samples that precede
    `start`, so the last sample leads straight into the first.
    """
    loop = leads[:, start:end].copy()
    for i in range(LOOP_FADE):
        t = (i + 1) / (LOOP_FADE + 1)
        loop[:, -LOOP_FADE + i] = (1 - t) * leads[:, end - LOOP_FADE + i] + t * leads[:, start - LOOP_FADE + i]
    return loop


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

    # Per-patient facts that need every lead before they can be decided.
    #   distinct:      single-lead sources (Cardially VFib, the original
    #                  dataset) were stored with one signal copied into all 12
    #                  lead slots. Counting distinct rows exposes that.
    #   prenormalized: the original dataset's rows all peak at exactly 1.0, so
    #                  their millivolt scale is gone. Checked per patient, not
    #                  per lead: PhysioNet values are quantized to 0.001 mV, so
    #                  a single calibrated lead can peak at exactly 1.0 by chance.
    distinct = {}
    prenormalized = {}
    for raw_signal, label in zip(signals, labels):
        key = (label[0], label[1])
        distinct.setdefault(key, set()).add(tuple(raw_signal))
        peaks_at_one = abs(max(abs(v) for v in raw_signal) - 1.0) < 1e-6
        prenormalized[key] = prenormalized.get(key, True) and peaks_at_one

    # Pass 1: filter, downsample and normalize every lead.
    patients = {}   # (rhythm, patient) -> { lead: (signal, peak) }
    total = len(signals)
    for i, (raw_signal, label) in enumerate(zip(signals, labels)):
        rhythm = label[0]       # e.g. "SR", "AFIB", "PACE", "SVTAC"
        patient = label[1]      # e.g. "5", "7", "15"
        lead = label[2]         # e.g. "I", "II", "V1"
        print(f"  [{i + 1}/{total}] {rhythm} patient {patient} lead {lead}")
        patients.setdefault((rhythm, patient), {})[lead] = process_signal(raw_signal)

    # Pass 2: one loop cut per patient. It has to be the same for every lead —
    # the 12 leads are simultaneous views of the same heartbeats.
    for (rhythm, patient), leads in patients.items():
        names = list(leads)
        stack = np.array([leads[name][0] for name in names])
        start, end = find_loop(stack, OUTPUT_FS)
        looped = make_loop(stack, start, end)
        print(f"  {rhythm} patient {patient}: loop {start}–{end} "
              f"({(end - start) / OUTPUT_FS:.2f} s of {stack.shape[1] / OUTPUT_FS:.0f} s)")

        entry = {"nLeads": len(distinct[(rhythm, patient)]), "leads": {}}
        for name, signal in zip(names, looped):
            lead_entry = {
                # Round to 3 decimal places to reduce JSON size
                "signal": [round(float(v), 3) for v in signal],
                "rPeaks": [int(p) for p in detect_r_peaks(signal, OUTPUT_FS)],
            }
            if not prenormalized[(rhythm, patient)]:
                lead_entry["mv"] = round(leads[name][1], 4)
            entry["leads"][name] = lead_entry

        output["rhythms"].setdefault(rhythm, {"patients": {}})["patients"][patient] = entry

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
