/**
 * audio.js — Web Audio API sound effects.
 *
 * Handles two types of audio:
 * 1. Heartbeat beep — short tone on each R-peak, pitch varies with SpO2
 *    (higher SpO2 = higher pitch, mimicking real pulse oximeters)
 * 2. Flatline alarm — alternating 800/1000 Hz tones during cardiac arrest
 * 3. Technical alarm — soft double chirp when the vest electrodes are off.
 *    Deliberately quieter, lower, and far less frequent than the flatline
 *    alarm: real monitors separate a HIGH-priority physiological alarm (the
 *    patient is dying) from a LOW-priority technical one (a lead fell off),
 *    and a student must be able to tell them apart without looking.
 *
 * Design decisions:
 * - AudioContext is created lazily on first user gesture (ensureAudio) because
 *   browsers block autoplay. We call ensureAudio() from startMonitor() which
 *   is always triggered by a button click.
 * - Alarm state machine (alarmOscActive, alarmToggle, alarmTimeout) is kept
 *   private to this module — only startAlarm/stopAlarm are exported.
 * - Uses oscillator nodes (not audio files) so there's zero loading latency
 *   and the pitch can be dynamically controlled.
 */

import state from './state.js';

// Private alarm state machine
let alarmOscActive = false;
let alarmToggle = false;
let alarmTimeout = null;

// Private technical-alarm state machine (vest electrodes off)
let techAlarmActive = false;
let techTimeout = null;

/**
 * Create or resume the Web Audio context. Must be called from a user gesture
 * (click/keypress) due to browser autoplay policies.
 */
export function ensureAudio() {
  if (!state.audioCtx) state.audioCtx = new (window.AudioContext || window.webkitAudioContext)();
  if (state.audioCtx.state === 'suspended') state.audioCtx.resume();
}

/**
 * Play a sine wave tone with exponential decay (avoids click artifacts).
 * Silently returns if muted or AudioContext not initialized.
 *
 * @param {number} freq - Frequency in Hz
 * @param {number} durationMs - Duration in milliseconds
 * @param {number} volume - Gain value (0.0 to 1.0)
 */
export function playTone(freq, durationMs, volume) {
  if (state.muted || !state.audioCtx) return;
  const osc = state.audioCtx.createOscillator();
  const gain = state.audioCtx.createGain();
  osc.type = 'sine';
  osc.frequency.value = freq;
  gain.gain.setValueAtTime(volume, state.audioCtx.currentTime);
  // Exponential ramp to near-zero avoids audible click at end of tone
  gain.gain.exponentialRampToValueAtTime(0.001, state.audioCtx.currentTime + durationMs / 1000);
  osc.connect(gain);
  gain.connect(state.audioCtx.destination);
  osc.start();
  osc.stop(state.audioCtx.currentTime + durationMs / 1000 + 0.01);
}

/**
 * Play the heartbeat beep sound. Pitch scales linearly with SpO2:
 *   SpO2  0% → 400 Hz (low, ominous)
 *   SpO2 100% → 1000 Hz (high, reassuring)
 * This mimics real pulse oximeters where clinicians can hear desaturation
 * without looking at the screen.
 *
 * @param {number} spo2 - Current SpO2 percentage (0-100)
 */
export function playHeartbeepBeep(spo2) {
  if (state.muted) return;
  ensureAudio();
  const freq = 400 + (spo2 / 100) * 600;
  playTone(Math.max(400, Math.min(1000, freq)), 80, 0.2);
}

/** Internal: plays one tick of the alarm, then schedules the next. */
function playAlarmTick() {
  if (!alarmOscActive) return;
  if (!state.muted && !state.alarmSilenced) {
    ensureAudio();
    // Alternates between 1000Hz and 800Hz to create urgency
    playTone(alarmToggle ? 1000 : 800, 300, 0.35);
    alarmToggle = !alarmToggle;
  }
  alarmTimeout = setTimeout(playAlarmTick, 800);
}

/**
 * Start the flatline alarm. Plays alternating tones every 800ms until stopped.
 * No-op if alarm is already running.
 */
export function startAlarm() {
  if (alarmOscActive) return;
  alarmOscActive = true;
  alarmToggle = false;
  playAlarmTick();
}

/**
 * Stop the flatline alarm immediately and clear any pending tick.
 */
export function stopAlarm() {
  alarmOscActive = false;
  if (alarmTimeout) { clearTimeout(alarmTimeout); alarmTimeout = null; }
}

/** Internal: two soft chirps, then schedules the next pair. */
function playTechTick() {
  if (!techAlarmActive) return;
  if (!state.muted && !state.alarmSilenced) {
    ensureAudio();
    playTone(440, 120, 0.12);
    setTimeout(() => { if (techAlarmActive) playTone(440, 120, 0.12); }, 190);
  }
  techTimeout = setTimeout(playTechTick, 4000);
}

/**
 * Start the technical alarm (vest electrodes not seated). Low priority:
 * a quiet double chirp every 4 seconds, versus the flatline alarm's urgent
 * 800/1000 Hz alternation every 800ms. No-op if already running.
 */
export function startTechnicalAlarm() {
  if (techAlarmActive) return;
  techAlarmActive = true;
  playTechTick();
}

/** Stop the technical alarm immediately and clear any pending chirp. */
export function stopTechnicalAlarm() {
  techAlarmActive = false;
  if (techTimeout) { clearTimeout(techTimeout); techTimeout = null; }
}
