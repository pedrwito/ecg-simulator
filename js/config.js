/**
 * config.js — Immutable constants, registries, and configuration.
 *
 * This file is the single source of truth for all values that don't change
 * at runtime. Everything here is exported as a constant or frozen object.
 *
 * Design decisions:
 * - DEFAULTS lives here (not scattered across session.js, ui.js, etc.) so that
 *   adding a new parameter means updating one place.
 * - RHYTHMS is a data-driven registry instead of if/else chains. Adding a new
 *   rhythm (e.g. "Flutter Auricular") means adding one entry here and one case
 *   in signals.js — nothing else changes.
 * - WAVEFORMS is a config table so the main loop doesn't hardcode 4 draw calls.
 *   Adding a 5th waveform (e.g. invasive BP) = one entry here + one canvas in
 *   HTML + one generator in signals.js.
 */

// --- Supabase credentials ---
// The anon key is designed to be public (client-side). Row Level Security (RLS)
// in Supabase is what actually protects the data.
export const SUPABASE_URL = 'https://jkppdinnqjcajmhqkuiw.supabase.co';
export const SUPABASE_ANON_KEY = 'sb_publishable_98tRM7fJEHLe4lj9DuahRA_lElvbtzW';

// --- Waveform sampling & display constants ---

/** Virtual sample rate in Hz. 150 is enough for visual fidelity on a monitor
 *  display while keeping CPU usage low (real bedside monitors use ~250Hz). */
export const FS = 150;

/** ECG and PPG use a 5-second sweep window (standard for bedside monitors). */
export const ECG_WIN = 5;

/** Respiration and CO2 use a 20-second window (slower waveforms need more context). */
export const RESP_WIN = 20;

/** Buffer sizes in samples — pre-computed from window × sample rate. */
export const ECG_LEN = ECG_WIN * FS;     // 750 samples
export const RESP_LEN = RESP_WIN * FS;   // 3000 samples

/** R-peak detection: minimum 300ms between beeps to avoid double-triggers. */
export const REFRACTORY = Math.round(0.3 * FS);

/** R-peak detection: amplitude threshold. The synthetic ECG R-wave peaks at 1.0,
 *  so 0.4 catches the upstroke reliably without triggering on T-waves (~0.25). */
export const ECG_THRESH = 0.4;

// --- Default parameter values ---
// Single source of truth. Used by session.js (create/join), ui.js (individual
// mode), and as fallback when parsing user input fails.
export const DEFAULTS = {
  rhythm: 'Ritmo Sinusal',
  lead: 'II',
  hr: 72,       // bpm — normal resting heart rate
  spo2: 98,     // % — normal oxygen saturation
  rr: 14,       // rpm — normal respiratory rate
  etco2: 38,    // mmHg — normal end-tidal CO2
  sys: 120,     // mmHg — normal systolic blood pressure
  dia: 80,      // mmHg — normal diastolic blood pressure
  temp: 36.6,   // °C — normal body temperature
};

// --- Signal source toggle ---
//
// Controls whether recording-based rhythms (AFib, Pacemaker, SVT) use real
// pre-processed patient ECG data or fall back to synthetic generation.
//
//   true  → Real recordings from data/signals.json (default)
//           Pros: physiologically accurate morphology, real fibrillatory
//           baseline, lead-specific waveforms, pacemaker spikes visible.
//           Cons: HR is inherent to the recording (not adjustable).
//
//   false → Synthetic Gaussian PQRST model for all rhythms
//           Pros: HR is dynamically adjustable, no data file needed.
//           Cons: simplified morphology, no lead differences, AFib is just
//           irregular R-R with noise (no real fibrillatory waves).
//
// When false, rhythms with a `syntheticFallback` config in the RHYTHMS
// registry will use those flags (e.g. AFib gets noP + jitter).
//
// To toggle: change this value and refresh. No rebuild needed.
export const USE_RECORDINGS = true;

// --- Rhythm registry ---
// Each rhythm can define optional flags:
//   source:   'synthetic' or 'recording' — where the ECG comes from.
//             If USE_RECORDINGS is false, all rhythms fall back to synthetic.
//   dataKey:  string — key in data/signals.json for recording-based rhythms
//   syntheticFallback: object — flags for synthetic generation when recordings
//             are disabled (noP, jitter, hrMin, hrMax)
//   noP:      boolean — suppress the P wave (synthetic only)
//   jitter:   boolean — randomize R-R intervals (synthetic only)
//   hrMin:    number  — minimum HR enforced when this rhythm is selected
//   hrMax:    number  — maximum HR enforced when this rhythm is selected
//
// Synthetic rhythms: ECG generated from Gaussian PQRST model, HR is dynamic.
// Recording rhythms: ECG from pre-processed real patient data, HR is inherent
// to the recording (not adjustable). PPG is generated from the recording's
// pre-computed R-peak positions for beat-to-beat synchronization.
export const RHYTHMS = {
  'Ritmo Sinusal':             { source: 'synthetic' },
  'Taquicardia':               { source: 'synthetic', hrMin: 140 },
  'Bradicardia':               { source: 'synthetic', hrMax: 45 },
  'Fibrilación Auricular':     {
    source: 'recording', dataKey: 'AFIB',
    // Synthetic fallback: no P wave + irregular R-R intervals
    syntheticFallback: { noP: true, jitter: true },
  },
  'Marcapasos':                { source: 'recording', dataKey: 'PACE' },
  'Taquicardia Supraventricular': { source: 'recording', dataKey: 'SVTAC' },
};

/**
 * Clamp a heart rate value to the valid range for a given rhythm.
 * This replaces duplicated if/else blocks that were in startMonitor and
 * professorApplyChanges.
 * @param {number} hr - Heart rate in bpm
 * @param {string} rhythm - Rhythm name (must be a key in RHYTHMS)
 * @returns {number} Clamped heart rate
 */
export function clampHRForRhythm(hr, rhythm) {
  const r = RHYTHMS[rhythm];
  if (!r) return hr;
  if (r.hrMin !== undefined) hr = Math.max(hr, r.hrMin);
  if (r.hrMax !== undefined) hr = Math.min(hr, r.hrMax);
  return hr;
}

// --- Waveform display configuration ---
// Each entry maps a waveform to its canvas, buffer, color, and Y-axis range.
// The main loop iterates this table instead of hardcoding 4 drawWaveform() calls.
// yMaxDynamic: if true, yMax is computed from CFG.etco2 at render time (CO2
// waveform amplitude depends on the user-configured EtCO2 value).
export const WAVEFORMS = [
  { canvasId: 'canvas-ecg',  bufKey: 'ecgBuf',  wpKey: 'ecgWritePos',  label: 'ECG',   color: '#00FF00', yMin: -0.5, yMax: 1.2 },
  { canvasId: 'canvas-ppg',  bufKey: 'ppgBuf',  wpKey: 'ecgWritePos',  label: 'Pleth', color: '#00FFFF', yMin: -0.2, yMax: 1.4 },
  { canvasId: 'canvas-resp', bufKey: 'respBuf', wpKey: 'respWritePos', label: 'RESP',  color: '#FFFF00', yMin: -1.5, yMax: 1.5 },
  { canvasId: 'canvas-co2',  bufKey: 'co2Buf',  wpKey: 'respWritePos', label: 'CO2',   color: '#FFFFFF', yMin: -3,   yMaxDynamic: true },
];
