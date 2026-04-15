/**
 * signals.js — Signal generators and real-signal data loader.
 *
 * Two signal sources:
 *
 * 1. SYNTHETIC (Ritmo Sinusal, Taquicardia, Bradicardia):
 *    ECG built from Gaussian PQRST model. HR is dynamically adjustable.
 *    PPG generated independently at the same HR.
 *
 * 2. RECORDING (AFib, Pacemaker, SVT):
 *    ECG loaded from pre-processed patient recordings (data/signals.json).
 *    PPG generated from the recording's pre-computed R-peak positions, so
 *    each PPG pulse is synchronized to the actual heartbeat in the ECG —
 *    including irregular rhythms like AFib where R-R intervals vary.
 *
 * All generators return Float32Arrays. The recording loader caches the JSON
 * data so it's only fetched once.
 */

import { RHYTHMS } from './config.js';

// ─── Recording data cache ──────────────────────────────────────────────────

let _signalDataCache = null;
let _signalDataPromise = null;

/**
 * Load pre-processed signal data from data/signals.json.
 * Fetched once and cached. Returns the full data object.
 * @returns {Promise<Object>} The parsed signals.json content
 */
export async function loadSignalData() {
  if (_signalDataCache) return _signalDataCache;
  if (_signalDataPromise) return _signalDataPromise;

  _signalDataPromise = fetch('data/signals.json')
    .then(r => {
      if (!r.ok) throw new Error(`Failed to load signals: ${r.status}`);
      return r.json();
    })
    .then(data => {
      _signalDataCache = data;
      return data;
    });

  return _signalDataPromise;
}

/**
 * Get a specific recording from the cached signal data.
 * Picks a random patient if multiple are available for the rhythm.
 *
 * @param {string} dataKey - Rhythm key in the JSON (e.g. 'AFIB', 'PACE', 'SVTAC')
 * @param {string} lead - Lead name (e.g. 'II', 'V1'). Falls back to 'II' if not found.
 * @returns {{ signal: number[], rPeaks: number[] } | null}
 */
export function getRecording(dataKey, lead) {
  if (!_signalDataCache) return null;
  const rhythmData = _signalDataCache.rhythms[dataKey];
  if (!rhythmData) return null;

  // Pick a random patient
  const patientIds = Object.keys(rhythmData.patients);
  const patientId = patientIds[Math.floor(Math.random() * patientIds.length)];
  const patient = rhythmData.patients[patientId];

  // Try requested lead, then uppercase variant (HTML uses "aVR" but data has "AVR"),
  // then fall back to II, then first available
  let leadData = patient.leads[lead];
  if (!leadData) leadData = patient.leads[lead.toUpperCase()];
  if (!leadData) leadData = patient.leads['II'];
  if (!leadData) leadData = patient.leads[Object.keys(patient.leads)[0]];

  return leadData || null;
}

/**
 * Generate a PPG signal synchronized to given R-peak positions.
 *
 * Instead of generating PPG at a fixed rate (which would be out of sync with
 * irregular rhythms like AFib), this places a PPG pulse after each R-peak with
 * a physiological delay (~250ms — the time for the pulse wave to travel from
 * heart to fingertip).
 *
 * Each pulse uses the same systolic+diastolic Gaussian model as generatePPG(),
 * but its duration adapts to the actual R-R interval, and amplitude varies
 * slightly with R-R length (shorter intervals = weaker pulse, per the
 * Frank-Starling mechanism).
 *
 * @param {number} totalSamples - Total number of output samples
 * @param {number} fs - Sample rate in Hz
 * @param {number[]} rPeaks - R-peak indices in the ECG signal (at same fs)
 * @returns {Float32Array} PPG signal synchronized to heartbeats
 */
export function generatePPGFromRPeaks(totalSamples, fs, rPeaks) {
  const signal = new Float32Array(totalSamples);
  if (rPeaks.length < 2) return signal;

  // Pulse wave transit delay: ~250ms from heart to finger
  const delaysamples = Math.round(0.25 * fs);

  // Compute median R-R for amplitude scaling reference
  const rrIntervals = [];
  for (let i = 1; i < rPeaks.length; i++) {
    rrIntervals.push(rPeaks[i] - rPeaks[i - 1]);
  }
  const medianRR = rrIntervals.slice().sort((a, b) => a - b)[Math.floor(rrIntervals.length / 2)];

  for (let i = 0; i < rPeaks.length; i++) {
    // Determine pulse duration from R-R interval
    const rr = (i < rPeaks.length - 1)
      ? rPeaks[i + 1] - rPeaks[i]
      : (i > 0 ? rPeaks[i] - rPeaks[i - 1] : Math.round(fs * 0.8));

    // Amplitude varies with R-R (Frank-Starling: longer fill time = stronger beat)
    const ampScale = Math.min(1.2, Math.max(0.4, rr / medianRR));

    const pulseStart = rPeaks[i] + delaysamples;
    const pulseDuration = Math.round(rr * 0.85); // pulse occupies ~85% of R-R

    for (let j = 0; j < pulseDuration; j++) {
      const outIdx = (pulseStart + j) % totalSamples;
      const t = j / pulseDuration;
      // Systolic peak + dicrotic notch (same shape as synthetic PPG)
      const sys = Math.exp(-((t - 0.22) ** 2) / (2 * 0.055 ** 2));
      const dia = 0.35 * Math.exp(-((t - 0.44) ** 2) / (2 * 0.065 ** 2));
      signal[outIdx] = ampScale * (sys + dia);
    }
  }

  return signal;
}

/**
 * Generate a synthetic ECG signal.
 *
 * Each heartbeat is modeled as 5 Gaussian curves (P, Q, R, S, T waves) with
 * parameters {a: amplitude, c: center position within beat, s: width (sigma)}.
 *
 * For atrial fibrillation: P wave is suppressed (no organized atrial activity)
 * and R-R intervals are randomized with 0.6-1.4x jitter to simulate the
 * characteristic "irregularly irregular" rhythm.
 *
 * @param {number} duration - Signal duration in seconds
 * @param {number} fs - Sample rate in Hz
 * @param {number} hr - Heart rate in beats per minute
 * @param {string} rhythm - Rhythm name (key in RHYTHMS registry)
 * @returns {Float32Array} ECG signal samples
 */
export function generateECG(duration, fs, hr, rhythm) {
  const N = duration * fs;
  const signal = new Float32Array(N);
  const beatSamples = Math.round((60 / hr) * fs);

  const rhythmDef = RHYTHMS[rhythm] || {};

  // PQRST wave definitions: a=amplitude, c=center (0-1 within beat), s=width (sigma)
  const waves = [
    { a:  0.18, c: 0.16, s: 0.055 },  // P wave  — atrial depolarization
    { a: -0.10, c: 0.27, s: 0.015 },  // Q wave  — septal depolarization
    { a:  1.00, c: 0.30, s: 0.015 },  // R wave  — ventricular depolarization peak
    { a: -0.18, c: 0.33, s: 0.015 },  // S wave  — late ventricular depolarization
    { a:  0.25, c: 0.52, s: 0.070 },  // T wave  — ventricular repolarization
  ];

  // AFib: suppress P wave (no organized atrial activity)
  if (rhythmDef.noP) {
    waves[0].a = 0;
  }

  if (rhythmDef.jitter) {
    // Irregular rhythm: each beat has a random duration (0.6x to 1.4x normal)
    let pos = 0;
    while (pos < N) {
      const jitter = 0.6 + Math.random() * 0.8;
      const thisBeat = Math.round(beatSamples * jitter);
      for (let j = 0; j < thisBeat && pos + j < N; j++) {
        const t = j / thisBeat;
        let v = 0;
        for (const w of waves) {
          const d = t - w.c;
          v += w.a * Math.exp(-(d * d) / (2 * w.s * w.s));
        }
        // Higher noise for AFib to simulate fibrillatory baseline
        v += 0.03 * (Math.random() - 0.5);
        signal[pos + j] = v;
      }
      pos += thisBeat;
    }
  } else {
    // Regular rhythm: build one beat template, then tile it
    const tmpl = new Float32Array(beatSamples);
    for (let j = 0; j < beatSamples; j++) {
      const t = j / beatSamples;
      let v = 0;
      for (const w of waves) {
        const d = t - w.c;
        v += w.a * Math.exp(-(d * d) / (2 * w.s * w.s));
      }
      tmpl[j] = v;
    }
    for (let i = 0; i < N; i++) {
      signal[i] = tmpl[i % beatSamples];
    }
  }

  // Add subtle baseline noise to all rhythms for visual realism
  for (let i = 0; i < N; i++) {
    signal[i] += 0.008 * (Math.random() - 0.5);
  }

  return signal;
}

/**
 * Generate a synthetic photoplethysmography (PPG / pulse oximeter) signal.
 *
 * Each beat has two Gaussian peaks: a systolic peak (main pulse from heart
 * contraction) and a smaller diastolic peak (reflected wave from arterial
 * compliance). This produces the characteristic double-hump waveform.
 *
 * @param {number} duration - Signal duration in seconds
 * @param {number} fs - Sample rate in Hz
 * @param {number} hr - Heart rate in bpm
 * @returns {Float32Array} PPG signal samples
 */
export function generatePPG(duration, fs, hr) {
  const N = duration * fs;
  const signal = new Float32Array(N);
  const beatSamples = Math.round((60 / hr) * fs);

  const tmpl = new Float32Array(beatSamples);
  for (let j = 0; j < beatSamples; j++) {
    const t = j / beatSamples;
    const sys = Math.exp(-((t - 0.22) ** 2) / (2 * 0.055 ** 2));           // systolic peak
    const dia = 0.35 * Math.exp(-((t - 0.44) ** 2) / (2 * 0.065 ** 2));    // dicrotic notch / diastolic peak
    tmpl[j] = sys + dia;
  }

  for (let i = 0; i < N; i++) {
    signal[i] = tmpl[i % beatSamples] + 0.005 * (Math.random() - 0.5);
  }
  return signal;
}

/**
 * Generate a synthetic respiratory impedance signal.
 * Simple sinusoidal model — sufficient for educational display.
 *
 * @param {number} duration - Signal duration in seconds
 * @param {number} fs - Sample rate in Hz
 * @param {number} rr - Respiratory rate in breaths per minute
 * @returns {Float32Array} Respiratory signal samples
 */
export function generateResp(duration, fs, rr) {
  const N = duration * fs;
  const signal = new Float32Array(N);
  const freq = rr / 60;
  for (let i = 0; i < N; i++) {
    signal[i] = Math.sin(2 * Math.PI * freq * i / fs) + 0.01 * (Math.random() - 0.5);
  }
  return signal;
}

/**
 * Generate a synthetic capnography (CO2) waveform.
 *
 * Models the 4 phases of a breath cycle:
 *   1. Inspiratory baseline (CO2 ≈ 0) — 40% of cycle
 *   2. Expiratory upstroke (0 → EtCO2) — 8% of cycle
 *   3. Alveolar plateau (≈ EtCO2) — 35% of cycle
 *   4. Inspiratory downstroke (EtCO2 → 0) — remaining 17%
 *
 * Phase fractions approximate a normal adult capnogram.
 *
 * @param {number} duration - Signal duration in seconds
 * @param {number} fs - Sample rate in Hz
 * @param {number} rr - Respiratory rate in breaths per minute
 * @param {number} etco2 - End-tidal CO2 in mmHg (plateau height)
 * @returns {Float32Array} CO2 signal samples
 */
export function generateCO2(duration, fs, rr, etco2) {
  const N = duration * fs;
  const signal = new Float32Array(N);
  if (rr <= 0) return signal;

  const breathSamples = Math.round((60 / rr) * fs);
  if (breathSamples < 4) return signal;

  const inspFrac = 0.40, upFrac = 0.08, platFrac = 0.35;

  for (let start = 0; start < N; start += breathSamples) {
    const n = Math.min(breathSamples, N - start);
    const nInsp = Math.round(n * inspFrac);
    const nUp   = Math.round(n * upFrac);
    const nPlat = Math.round(n * platFrac);
    const nDown = n - nInsp - nUp - nPlat;

    // Phase 1: inspiratory baseline stays at 0 (already initialized)
    // Phase 2: expiratory upstroke
    let idx = start + nInsp;
    for (let j = 0; j < nUp && idx + j < N; j++) {
      signal[idx + j] = etco2 * (j / nUp);
    }
    // Phase 3: alveolar plateau
    idx += nUp;
    for (let j = 0; j < nPlat && idx + j < N; j++) {
      signal[idx + j] = etco2;
    }
    // Phase 4: inspiratory downstroke
    idx += nPlat;
    for (let j = 0; j < nDown && idx + j < N; j++) {
      signal[idx + j] = etco2 * (1 - j / nDown);
    }
  }
  return signal;
}
