/**
 * monitor.js — Core runtime engine: animation loop, signal playback, and arrest logic.
 *
 * This is the "heart" of the application. It ties together signals, audio, and
 * canvas rendering into a real-time animation loop, and manages the cardiac
 * arrest simulation.
 *
 * Architecture:
 * - 60 seconds of signal data are pre-generated (regenerateSignals) and stored
 *   in state.ecgFull/ppgFull/respFull/co2Full.
 * - The animation loop (frame) runs via requestAnimationFrame and copies samples
 *   from the pre-generated arrays into circular buffers at the virtual sample
 *   rate (150 Hz). Frame-rate independence is achieved by accumulating fractional
 *   samples based on elapsed wall-clock time.
 * - Circular buffers are rendered by canvas.js with a sweep-gap effect.
 * - startMonitor() is the shared entry point for all 3 modes (professor, student,
 *   individual). It initializes buffers, generates signals, resets UI, and kicks
 *   off the animation loop.
 * - stopAndCleanup() centralizes the teardown that was previously duplicated 4×
 *   across different return/navigation paths.
 */

import state from './state.js';
import { FS, ECG_LEN, RESP_LEN, ECG_THRESH, REFRACTORY, WAVEFORMS, RHYTHMS, DEFAULTS, USE_RECORDINGS, clampHRForRhythm } from './config.js';
import { generateECG, generatePPG, generateVFib, generateResp, generateCO2, loadSignalData, getRecording, generatePPGFromRPeaks } from './signals.js';
import { ensureAudio, playTone, playHeartbeepBeep, startAlarm, stopAlarm } from './audio.js';
import { drawWaveform } from './canvas.js';

/**
 * Pre-generate or load all waveform signals from current CFG parameters.
 *
 * For synthetic rhythms (sinus, tachy, brady): generates 60s of ECG + PPG
 * mathematically. HR is dynamically controllable.
 *
 * For recording rhythms (AFib, pacemaker, SVT): loads pre-processed ECG from
 * data/signals.json and generates PPG synchronized to the recording's R-peaks.
 * The recording is looped to fill 60s. HR is inherent to the recording.
 *
 * Respiratory and CO2 waveforms are always synthetic (simple sinusoidal /
 * trapezoidal models that work well enough for education).
 */
// Track previous parameter values to detect what actually changed
let _prevHr = null;
let _prevRhythm = null;
let _prevLead = null;
let _prevRr = null;
let _prevEtco2 = null;

/**
 * Regenerate only the signals whose parameters actually changed.
 *
 * - Rhythm, HR, or lead change → regenerate ECG + PPG (PPG always follows ECG)
 * - RR change → regenerate RESP + CO2
 * - EtCO2 change → regenerate CO2 only
 * - No buffer clearing — the sweep naturally overwrites old data with new,
 *   creating a smooth live transition just like a real bedside monitor.
 *
 * @param {boolean} [forceAll=false] - If true, regenerate everything (used on first start)
 */
export function regenerateSignals(forceAll = false) {
  const rhythmChanged = forceAll || state.CFG.rhythm !== _prevRhythm;
  const hrChanged     = forceAll || state.CFG.hr !== _prevHr;
  const leadChanged   = forceAll || state.CFG.lead !== _prevLead;
  const rrChanged     = forceAll || state.CFG.rr !== _prevRr;
  const etco2Changed  = forceAll || state.CFG.etco2 !== _prevEtco2;

  // ECG + PPG: regenerate if rhythm, HR, or lead changed
  if (rhythmChanged || hrChanged || leadChanged) {
    const rhythmDef = RHYTHMS[state.CFG.rhythm] || {};
    const source = rhythmDef.source || 'synthetic';
    const wantsRecording = (source === 'recording') && rhythmDef.dataKey;
    const useRecording = wantsRecording && USE_RECORDINGS;

    if (useRecording) {
      const recording = getRecording(rhythmDef.dataKey, state.CFG.lead || 'II');

      if (recording) {
        const recSignal = recording.signal;
        const recRPeaks = recording.rPeaks;
        const recLen = recSignal.length;

        // Loop the recording to fill 60 seconds
        const totalSamples = 60 * FS;
        const ecg = new Float32Array(totalSamples);
        const allRPeaks = [];

        for (let offset = 0; offset < totalSamples; offset += recLen) {
          const remaining = Math.min(recLen, totalSamples - offset);
          for (let i = 0; i < remaining; i++) {
            ecg[offset + i] = recSignal[i];
          }
          for (const rp of recRPeaks) {
            const adjustedIdx = offset + rp;
            if (adjustedIdx < totalSamples) {
              allRPeaks.push(adjustedIdx);
            }
          }
        }

        state.ecgFull = ecg;

        if (rhythmDef.noPulse) {
          // Pulseless rhythm (VFib): flat PPG, no cardiac output
          state.ppgFull = new Float32Array(totalSamples);
          state.CFG.hr = 0;
        } else {
          state.ppgFull = generatePPGFromRPeaks(totalSamples, FS, allRPeaks);

          // Compute actual HR from the recording's R-peaks
          if (allRPeaks.length >= 2) {
            const totalBeats = allRPeaks.length - 1;
            const totalTime = (allRPeaks[allRPeaks.length - 1] - allRPeaks[0]) / FS;
            state.CFG.hr = Math.round(60 * totalBeats / totalTime);
          }
        }
      } else {
        state.ecgFull = generateECG(60, FS, state.CFG.hr, state.CFG.rhythm);
        state.ppgFull = generatePPG(60, FS, state.CFG.hr);
      }
    } else {
      // Synthetic — apply fallback flags if needed
      if (wantsRecording && !useRecording && rhythmDef.syntheticFallback) {
        Object.assign(rhythmDef, rhythmDef.syntheticFallback);
      }
      state.ecgFull = generateECG(60, FS, state.CFG.hr, state.CFG.rhythm);
      state.ppgFull = generatePPG(60, FS, state.CFG.hr);
    }

    // Reset ECG read position and R-peak detection state
    state.ecgSampleIdx = 0;
    state.prevEcg = 0;
    state.samplesSinceBeep = REFRACTORY;
  }

  // RESP: regenerate if respiratory rate changed
  if (rrChanged) {
    state.respFull = generateResp(60, FS, state.CFG.rr);
    state.respSampleIdx = 0;
  }

  // CO2: regenerate if respiratory rate or EtCO2 changed
  if (rrChanged || etco2Changed) {
    state.co2Full = generateCO2(60, FS, state.CFG.rr, state.CFG.etco2);
    // Only reset resp read position if not already reset by rrChanged above
    if (!rrChanged) state.respSampleIdx = 0;
  }

  // Update tracked values
  _prevHr = state.CFG.hr;
  _prevRhythm = state.CFG.rhythm;
  _prevLead = state.CFG.lead;
  _prevRr = state.CFG.rr;
  _prevEtco2 = state.CFG.etco2;
}

/**
 * Update all numeric parameter displays on the monitor screen.
 * MAP (Mean Arterial Pressure) is approximated as: diastolic + 1/3(systolic - diastolic).
 */
export function updateDisplays() {
  document.getElementById('val-hr').textContent = state.CFG.hr;
  document.getElementById('val-hr').style.color = '#00FF00';
  document.getElementById('val-spo2').textContent = state.CFG.spo2;
  document.getElementById('val-rr').textContent = state.CFG.rr;
  document.getElementById('val-etco2').textContent = state.CFG.etco2;
  document.getElementById('val-nibp').textContent = state.CFG.sys + '/' + state.CFG.dia;
  const map = Math.round(state.CFG.dia + (state.CFG.sys - state.CFG.dia) / 3);
  document.getElementById('val-map').textContent = '(' + map + ') mmHg';
  document.getElementById('val-temp').textContent = state.CFG.temp.toFixed(1);
}

/**
 * Main animation loop — called every frame via requestAnimationFrame.
 *
 * Frame-rate independent: calculates how many samples to write based on elapsed
 * wall-clock time × virtual sample rate (150 Hz). This means the waveform
 * scrolls at the correct speed regardless of whether the browser renders at
 * 30fps or 144fps.
 *
 * @param {DOMHighResTimeStamp} timestamp - Provided by requestAnimationFrame
 */
export function frame(timestamp) {
  if (!state.running) return;
  state.animFrameId = requestAnimationFrame(frame);

  // First frame: just record the timestamp, no samples to write yet
  if (!state.lastFrameTime) { state.lastFrameTime = timestamp; return; }
  const dt = (timestamp - state.lastFrameTime) / 1000;
  state.lastFrameTime = timestamp;

  // Accumulate fractional samples and write whole samples to buffers
  state.sampleAccum += dt * FS;
  const samplesToWrite = Math.floor(state.sampleAccum);
  state.sampleAccum -= samplesToWrite;

  for (let s = 0; s < samplesToWrite; s++) {
    // ECG/PPG and RESP/CO2 use independent read indices so changing
    // rhythm doesn't restart the respiratory waveform
    const ecgIdx = state.ecgSampleIdx % state.ecgFull.length;
    const respIdx = state.respSampleIdx % state.respFull.length;

    // During arrest: RESP and CO2 always flatline (patient isn't breathing).
    // ECG and PPG depend on the rhythm type and defib state.
    const currentRhythmDef = RHYTHMS[state.CFG.rhythm] || {};
    const isArrested = state.arrestActive;
    // ECG flatline only if arrested AND not a pulseless rhythm (VFib shows its waveform)
    // AND not during defib sequence (need to show shock artifact + recovery)
    const ecgFlatline = isArrested && !currentRhythmDef.noPulse && !_defibInProgress;

    if (ecgFlatline) {
      // Standard cardiac arrest: flatline ECG and PPG
      state.ecgBuf[state.ecgWritePos] = 0;
      state.ppgBuf[state.ecgWritePos] = 0;
    } else {
      // Normal or VFib or defib sequence: play ECG and PPG from signals
      const ecgVal = state.ecgFull[ecgIdx];
      state.ecgBuf[state.ecgWritePos] = ecgVal;
      state.ppgBuf[state.ecgWritePos] = state.ppgFull[ecgIdx];

      // R-peak detection: trigger heartbeat beep on upward threshold crossing
      // (skip during defib sequence — the recovery beats shouldn't beep yet)
      if (!_defibInProgress) {
        state.samplesSinceBeep++;
        if (state.prevEcg < ECG_THRESH && ecgVal >= ECG_THRESH && state.samplesSinceBeep >= REFRACTORY) {
          state.samplesSinceBeep = 0;
          playHeartbeepBeep(state.CFG.spo2);
        }
        state.prevEcg = ecgVal;
      }
    }

    // RESP and CO2: flatline during any arrest state (patient not breathing),
    // normal playback otherwise
    if (isArrested) {
      state.respBuf[state.respWritePos] = 0;
      state.co2Buf[state.respWritePos] = 0;
    } else {
      state.respBuf[state.respWritePos] = state.respFull[respIdx];
      state.co2Buf[state.respWritePos] = state.co2Full[respIdx];
    }

    // Advance circular buffer write positions (wrap around)
    state.ecgWritePos = (state.ecgWritePos + 1) % ECG_LEN;
    state.respWritePos = (state.respWritePos + 1) % RESP_LEN;
    state.ecgSampleIdx++;
    if (!isArrested) state.respSampleIdx++;
  }

  // Draw all waveforms using the WAVEFORMS config table
  for (const wf of WAVEFORMS) {
    const canvas = document.getElementById(wf.canvasId);
    const buffer = state[wf.bufKey];
    const writePos = state[wf.wpKey];
    // CO2 Y-axis max depends on current EtCO2 setting (yMaxDynamic flag)
    const yMax = wf.yMaxDynamic ? state.CFG.etco2 * 1.3 : wf.yMax;
    drawWaveform(canvas, buffer, writePos, wf.label, wf.color, wf.yMin, yMax);
  }

  // Update numeric displays (different during arrest)
  if (state.arrestActive) {
    document.getElementById('val-rr').textContent = '0';
    document.getElementById('val-etco2').textContent = '0';
    document.getElementById('val-spo2').textContent = Math.round(state.spo2Decay);
  } else {
    document.getElementById('val-hr').textContent = state.CFG.hr;
    document.getElementById('val-spo2').textContent = state.CFG.spo2;
    document.getElementById('val-rr').textContent = state.CFG.rr;
    document.getElementById('val-etco2').textContent = state.CFG.etco2;
  }
}

/**
 * Apply a set of parameter changes to the running monitor.
 * Used by both Supabase realtime sync (student receives professor's changes)
 * and professor's "Aplicar Cambios" button.
 *
 * Async because switching to a recording-based rhythm may require loading
 * signal data (usually already cached from preload, but just in case).
 *
 * Only updates fields that are present in the params object (undefined fields
 * are ignored), so partial updates work correctly.
 *
 * @param {Object} params - Parameter overrides (hr, spo2, rr, etco2, nibp_sys, nibp_dia, temp, rhythm, arrest)
 */
export async function applyParameters(params) {
  if (params.hr !== undefined)       state.CFG.hr     = params.hr;
  if (params.spo2 !== undefined)     state.CFG.spo2   = params.spo2;
  if (params.rr !== undefined)       state.CFG.rr     = params.rr;
  if (params.etco2 !== undefined)    state.CFG.etco2  = params.etco2;
  if (params.nibp_sys !== undefined) state.CFG.sys     = params.nibp_sys;
  if (params.nibp_dia !== undefined) state.CFG.dia     = params.nibp_dia;
  if (params.temp !== undefined)     state.CFG.temp    = params.temp;
  if (params.rhythm !== undefined)   state.CFG.rhythm  = params.rhythm;

  // Clamp HR for the selected rhythm (e.g. Taquicardia enforces >= 140)
  if (params.rhythm !== undefined || params.hr !== undefined) {
    state.CFG.hr = clampHRForRhythm(state.CFG.hr, state.CFG.rhythm);
  }

  // If the new rhythm uses recordings, ensure data is loaded
  const rhythmDef = RHYTHMS[state.CFG.rhythm] || {};
  if (USE_RECORDINGS && rhythmDef.source === 'recording' && rhythmDef.dataKey) {
    await loadSignalData();
  }

  // Handle arrest state transitions
  const newArrest = params.arrest !== undefined ? params.arrest : state.arrestActive;
  if (newArrest !== state.arrestActive) {
    if (newArrest) {
      activateArrest();
    } else {
      deactivateArrest();
    }
  }

  // Regenerate signals with new parameters (skip if arresting — no waveforms needed)
  if (!newArrest) {
    regenerateSignals();
    updateDisplays();
  }

  // If the rhythm has no pulse (e.g. VFib), activate arrest-like state
  // (alarm, defib button, SpO2 decay) but keep the ECG waveform playing
  if (rhythmDef.noPulse && !state.arrestActive) {
    activateArrest();
  }
}

// =========================================================================
//  CARDIAC ARREST SIMULATION
//  Activating arrest: flatlines all waveforms, starts alarm, SpO2 decays
//  toward 0 (1 point every 2 seconds), HR display flashes between red/dark.
// =========================================================================

/** Activate cardiac arrest mode. */
export function activateArrest() {
  state.arrestActive = true;
  const btnToolbar = document.getElementById('btn-arrest');
  const btnSidebar = document.getElementById('btn-sidebar-arrest');
  const indicator = document.getElementById('alarm-indicator');

  btnToolbar.classList.add('active');
  btnSidebar.classList.add('active');
  btnSidebar.textContent = 'Revertir Paro';
  indicator.style.display = 'block';
  state.spo2Decay = state.CFG.spo2;

  // Show defibrillator buttons
  document.getElementById('btn-defib').style.display = '';
  document.getElementById('btn-sidebar-defib').style.display = '';

  startAlarm();

  // SpO2 decays by 1% every 2 seconds during arrest
  state.spo2DecayInterval = setInterval(() => {
    if (state.arrestActive && state.spo2Decay > 0) state.spo2Decay = Math.max(0, state.spo2Decay - 1);
  }, 2000);

  // Alarm indicator and HR display flash every 500ms
  state.alarmFlashInterval = setInterval(() => {
    state.alarmFlashOn = !state.alarmFlashOn;
    indicator.style.background = state.alarmFlashOn ? '#660000' : '#330000';
    const hrEl = document.getElementById('val-hr');
    hrEl.textContent = '0';
    hrEl.style.color = state.alarmFlashOn ? '#FF0000' : '#330000';
  }, 500);

  document.getElementById('val-hr').textContent = '0';
  document.getElementById('val-hr').style.color = '#FF0000';
}

/** Deactivate cardiac arrest and restore normal operation.
 *  If the current rhythm was a pulseless rhythm (VFib), switch back to Sinus. */
export function deactivateArrest() {
  state.arrestActive = false;

  // If coming out of a pulseless rhythm (e.g. VFib after defib), return to sinus
  const rhythmDef = RHYTHMS[state.CFG.rhythm] || {};
  if (rhythmDef.noPulse) {
    state.CFG.rhythm = 'Ritmo Sinusal';
    state.CFG.hr = DEFAULTS.hr;
  }
  const btnToolbar = document.getElementById('btn-arrest');
  const btnSidebar = document.getElementById('btn-sidebar-arrest');
  const indicator = document.getElementById('alarm-indicator');

  btnToolbar.classList.remove('active');
  btnSidebar.classList.remove('active');
  btnSidebar.textContent = 'Paro Cardíaco';
  indicator.style.display = 'none';
  stopAlarm();
  clearInterval(state.spo2DecayInterval);
  clearInterval(state.alarmFlashInterval);
  state.spo2Decay = state.CFG.spo2;

  // Restore normal display values
  document.getElementById('val-hr').textContent = state.CFG.hr;
  document.getElementById('val-hr').style.color = '#00FF00';
  document.getElementById('val-spo2').textContent = state.CFG.spo2;
  document.getElementById('val-rr').textContent = state.CFG.rr;
  document.getElementById('val-etco2').textContent = state.CFG.etco2;

  // Force-regenerate all signals coming back from arrest flatline
  regenerateSignals(true);

  // Hide defib buttons
  document.getElementById('btn-defib').style.display = 'none';
  document.getElementById('btn-sidebar-defib').style.display = 'none';
}

// =========================================================================
//  DEFIBRILLATION
//  When triggered during cardiac arrest:
//  1. Inject a high-amplitude spike into the ECG buffer (the defib artifact)
//  2. Play a "shock" sound
//  3. Pause for ~2.5 seconds (post-shock assessment period)
//  4. Resume normal rhythm (deactivate arrest)
// =========================================================================

/** Whether a defibrillation sequence is currently in progress. */
let _defibInProgress = false;

/**
 * Trigger defibrillation. Only works during active cardiac arrest.
 * Injects a defibrillation artifact into the ECG waveform, then after a
 * brief pause, restores normal rhythm.
 */
/**
 * Trigger defibrillation. Only works during active cardiac arrest.
 *
 * Clinical sequence simulated:
 * 1. Defibrillation artifact — large biphasic spike on ECG (~150ms)
 *    visible as a sharp vertical deflection that clips the display
 * 2. Post-shock pause — ~3 seconds of near-flatline with small
 *    residual oscillations (normal: the heart is stunned)
 * 3. Rhythm recovery — normal sinus rhythm gradually returns
 *
 * The animation loop continues running during the sequence. We temporarily
 * override the ECG signal source to inject the artifact and post-shock
 * pause, then hand back to normal playback.
 */
export function triggerDefibrillation() {
  if (!state.arrestActive || _defibInProgress) return;
  _defibInProgress = true;

  // --- Phase 1: Shock sound + artifact ---
  ensureAudio();
  // Low thump + crackle simulating the capacitor discharge
  playTone(150, 100, 0.6);
  setTimeout(() => playTone(80, 200, 0.4), 80);

  // Generate a full post-shock ECG sequence:
  // [artifact ~150ms] [flatline with tiny residual noise ~3s] [sinus beats emerge]
  const artifactLen = Math.round(0.15 * FS);    // 150ms shock artifact
  const pauseLen = Math.round(3.0 * FS);         // 3s post-shock pause
  const recoveryLen = Math.round(2.0 * FS);      // 2s recovery with emerging beats
  const totalLen = artifactLen + pauseLen + recoveryLen;

  const postShockSignal = new Float32Array(totalLen);

  // Phase 1: Biphasic defibrillation artifact
  for (let i = 0; i < artifactLen; i++) {
    const t = i / artifactLen;
    if (t < 0.25) {
      postShockSignal[i] = 5.0 * (t / 0.25);               // rapid rise
    } else if (t < 0.5) {
      postShockSignal[i] = 5.0 * (1 - 2 * (t - 0.25));     // cross to -5
    } else {
      postShockSignal[i] = -5.0 * (1 - (t - 0.5) / 0.5);   // decay to 0
    }
  }

  // Phase 2: Post-shock pause (near-flat with small residual oscillations)
  for (let i = 0; i < pauseLen; i++) {
    const t = i / pauseLen;
    // Tiny decaying oscillation — stunned myocardium
    const decay = Math.exp(-t * 5);
    postShockSignal[artifactLen + i] = 0.05 * decay * Math.sin(2 * Math.PI * 3 * t)
                                      + 0.01 * (Math.random() - 0.5);
  }

  // Phase 3: Recovery — sinus beats emerge with increasing amplitude
  const recoveryHR = DEFAULTS.hr;
  const beatSamples = Math.round((60 / recoveryHR) * FS);
  const pulseDelay = Math.round(0.25 * FS); // PPG delay after R-peak (~250ms)

  for (let i = 0; i < recoveryLen; i++) {
    const t = (i % beatSamples) / beatSamples;
    const beatProgress = i / recoveryLen; // 0→1 over recovery period
    // Simplified PQRST that grows in amplitude
    const r = Math.exp(-((t - 0.30) ** 2) / (2 * 0.015 ** 2));
    const s = -0.18 * Math.exp(-((t - 0.33) ** 2) / (2 * 0.015 ** 2));
    const tWave = 0.25 * Math.exp(-((t - 0.52) ** 2) / (2 * 0.07 ** 2));
    const amp = 0.2 + 0.6 * beatProgress;
    postShockSignal[artifactLen + pauseLen + i] = amp * (r + s + tWave)
                                                  + 0.01 * (Math.random() - 0.5);
  }

  // Generate matching PPG: flat during artifact+pause, pulses during recovery
  const postShockPPG = new Float32Array(totalLen);
  // PPG pulses appear during recovery, delayed ~250ms from R-peaks
  for (let i = 0; i < recoveryLen; i++) {
    const delayedI = i - pulseDelay;
    if (delayedI < 0) continue;
    const t = (delayedI % beatSamples) / beatSamples;
    const beatProgress = i / recoveryLen;
    const sys = Math.exp(-((t - 0.22) ** 2) / (2 * 0.055 ** 2));
    const dia = 0.35 * Math.exp(-((t - 0.44) ** 2) / (2 * 0.065 ** 2));
    const amp = 0.2 + 0.6 * beatProgress;
    postShockPPG[artifactLen + pauseLen + i] = amp * (sys + dia);
  }

  // Inject both post-shock sequences
  state.ecgFull = postShockSignal;
  state.ppgFull = postShockPPG;
  state.ecgSampleIdx = 0;
  // Stop the VFib alarm during the post-shock sequence
  stopAlarm();

  // After the full sequence plays out (~5s), fully restore normal rhythm
  const sequenceDuration = (totalLen / FS) * 1000; // ms
  setTimeout(() => {
    _defibInProgress = false;
    deactivateArrest();
  }, sequenceDuration);
}

/**
 * Stop the animation loop and clear all timers/intervals.
 * Called when navigating away from the monitor screen. Centralizes the cleanup
 * that was previously duplicated in 4 different navigation paths.
 */
export function stopAndCleanup() {
  state.running = false;
  if (state.animFrameId) cancelAnimationFrame(state.animFrameId);
  stopAlarm();
  clearInterval(state.spo2DecayInterval);
  clearInterval(state.alarmFlashInterval);
  clearInterval(state.silenceInterval);
  state.arrestActive = false;
}

/**
 * Initialize and start the monitor. Shared entry point for all 3 modes.
 *
 * Async because recording-based rhythms need to fetch data/signals.json
 * on first use. The fetch is cached after the first call.
 *
 * 1. Pre-loads signal data if needed (recording rhythms)
 * 2. Clamps HR to valid range for the selected rhythm
 * 3. Pre-generates or loads 60s of all signal waveforms
 * 4. Allocates circular buffers
 * 5. Resets all UI elements to default state
 * 6. Configures UI visibility based on session mode
 * 7. Starts the animation loop (with 50ms delay to let the DOM settle)
 */
export async function startMonitor() {
  ensureAudio();

  // Pre-load signal recordings if this rhythm uses them and recordings are enabled
  const rhythmDef = RHYTHMS[state.CFG.rhythm] || {};
  if (USE_RECORDINGS && rhythmDef.source === 'recording' && rhythmDef.dataKey) {
    await loadSignalData();
  }

  // Clamp HR using rhythm registry (e.g. Taquicardia enforces HR >= 140)
  state.CFG.hr = clampHRForRhythm(state.CFG.hr, state.CFG.rhythm);

  // Force-regenerate all signals on initial start
  regenerateSignals(true);

  // Allocate circular buffers (ECG/PPG share size, RESP/CO2 share size)
  state.ecgBuf  = new Float32Array(ECG_LEN);
  state.ppgBuf  = new Float32Array(ECG_LEN);
  state.respBuf = new Float32Array(RESP_LEN);
  state.co2Buf  = new Float32Array(RESP_LEN);
  state.ecgWritePos = 0;
  state.respWritePos = 0;
  state.ecgSampleIdx = 0;
  state.respSampleIdx = 0;

  updateDisplays();

  // Reset all runtime flags and UI elements
  state.arrestActive = false;
  state.muted = false;
  state.alarmSilenced = false;
  state.spo2Decay = state.CFG.spo2;
  document.getElementById('btn-mute').textContent = 'Mute';
  document.getElementById('btn-arrest').classList.remove('active');
  document.getElementById('btn-sidebar-arrest').classList.remove('active');
  document.getElementById('btn-sidebar-arrest').textContent = 'Paro Cardíaco';
  document.getElementById('alarm-indicator').style.display = 'none';
  document.getElementById('btn-silence').textContent = 'Silenciar Alarma';
  document.getElementById('btn-defib').style.display = 'none';
  document.getElementById('btn-sidebar-defib').style.display = 'none';

  // Configure UI visibility based on session mode
  const sessionBar = document.getElementById('session-bar');
  const sidebar = document.getElementById('professor-sidebar');
  const btnArrest = document.getElementById('btn-arrest');

  if (state.sessionMode === 'professor') {
    sessionBar.style.display = 'block';
    document.getElementById('display-session-code').textContent = state.sessionCode;
    document.getElementById('display-session-role').textContent = '(Profesor)';
    document.getElementById('display-student-count').textContent = 'Alumnos conectados: 0';
    sidebar.style.display = 'block';
    btnArrest.style.display = 'none';     // professor uses sidebar button instead
  } else if (state.sessionMode === 'student') {
    sessionBar.style.display = 'block';
    document.getElementById('display-session-code').textContent = state.sessionCode;
    document.getElementById('display-session-role').textContent = '(Alumno)';
    document.getElementById('display-student-count').textContent = '';
    sidebar.style.display = 'none';
    btnArrest.style.display = 'none';     // students cannot trigger arrest
  } else {
    // Individual mode
    sessionBar.style.display = 'none';
    sidebar.style.display = 'none';
    btnArrest.style.display = '';          // show arrest button in toolbar
  }

  showMonitorScreen();

  // Small delay lets the DOM layout settle before starting animation
  // (prevents first-frame canvas size issues)
  setTimeout(() => {
    state.running = true;
    state.lastFrameTime = 0;
    state.sampleAccum = 0;
    state.animFrameId = requestAnimationFrame(frame);
  }, 50);
}

/**
 * Show the monitor screen. Duplicated from ui.js to avoid a circular import
 * (monitor.js → ui.js → monitor.js). Only handles the monitor-screen case.
 */
function showMonitorScreen() {
  ['landing-screen', 'join-dialog', 'config-screen', 'monitor-screen'].forEach(s => {
    document.getElementById(s).style.display = 'none';
  });
  const el = document.getElementById('monitor-screen');
  el.style.display = 'flex';
}
