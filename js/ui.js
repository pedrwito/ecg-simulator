/**
 * ui.js — Screen navigation, user input handling, and DOM event wiring.
 *
 * This is the "glue" layer between the user's clicks and the rest of the
 * application. It reads form inputs, calls the appropriate functions from
 * monitor.js and session.js, and manages screen transitions.
 *
 * Design decisions:
 * - All addEventListener calls are centralized in initEventListeners() rather
 *   than scattered across modules. This makes it easy to see all user
 *   interactions in one place.
 * - Navigation functions handle the different cleanup needs per mode:
 *   professor must confirm and close the session, student just disconnects,
 *   individual goes back to config screen.
 * - Alarm silence uses a 120-second countdown (standard clinical duration)
 *   after which the alarm automatically re-enables.
 */

import state from './state.js';
import { DEFAULTS } from './config.js';
import { startMonitor, stopAndCleanup, activateArrest, deactivateArrest, triggerDefibrillation } from './monitor.js';
import { createSession, joinSession, closeSession, cleanupSession, professorApplyChanges, professorToggleArrest, professorDefibrillate } from './session.js';
import { ensureAudio } from './audio.js';

/**
 * Show a screen by ID, hiding all others.
 * Non-monitor screens get centered flex layout applied inline (monitor screen
 * uses its own flex layout defined in CSS).
 *
 * @param {string} id - Screen element ID ('landing-screen', 'join-dialog', 'config-screen', 'monitor-screen')
 */
export function showScreen(id) {
  ['landing-screen', 'join-dialog', 'config-screen', 'monitor-screen'].forEach(s => {
    document.getElementById(s).style.display = 'none';
  });
  const el = document.getElementById(id);
  el.style.display = 'flex';
  if (id !== 'monitor-screen') {
    el.style.flexDirection = 'column';
    el.style.alignItems = 'center';
    el.style.justifyContent = 'center';
  }
}

/**
 * Start individual mode: read config screen inputs and launch the monitor.
 * Falls back to DEFAULTS if any input parsing fails.
 */
async function startIndividual() {
  state.sessionMode = 'individual';

  state.CFG.rhythm = document.getElementById('cfg-rhythm').value;
  state.CFG.lead   = document.getElementById('cfg-lead').value;
  state.CFG.hr     = parseInt(document.getElementById('cfg-hr').value) || DEFAULTS.hr;
  state.CFG.spo2   = parseInt(document.getElementById('cfg-spo2').value) || DEFAULTS.spo2;
  state.CFG.rr     = parseInt(document.getElementById('cfg-rr').value) || DEFAULTS.rr;
  state.CFG.etco2  = parseInt(document.getElementById('cfg-etco2').value) || DEFAULTS.etco2;
  state.CFG.sys    = parseInt(document.getElementById('cfg-sys').value) || DEFAULTS.sys;
  state.CFG.dia    = parseInt(document.getElementById('cfg-dia').value) || DEFAULTS.dia;
  state.CFG.temp   = parseFloat(document.getElementById('cfg-temp').value) || DEFAULTS.temp;

  await startMonitor();
}

/** Navigate back to the landing screen, cleaning up all state. */
function returnToLanding() {
  stopAndCleanup();
  cleanupSession();
  showScreen('landing-screen');
}

/**
 * Handle the "Volver" button from the monitor screen.
 * Different behavior per mode:
 * - Professor: confirm, then close session (disconnects all students)
 * - Student: just disconnect and return to landing
 * - Individual: return to config screen (can re-adjust and restart)
 */
function returnFromMonitor() {
  if (state.sessionMode === 'professor') {
    if (confirm('¿Cerrar la sesión? Los alumnos serán desconectados.')) {
      stopAndCleanup();
      closeSession();
      showScreen('landing-screen');
    }
  } else if (state.sessionMode === 'student') {
    stopAndCleanup();
    cleanupSession();
    showScreen('landing-screen');
  } else {
    stopAndCleanup();
    showScreen('config-screen');
  }
}

/**
 * Toggle cardiac arrest (individual mode only).
 * Professor and student modes use their own arrest controls.
 */
function toggleArrest() {
  if (state.sessionMode !== 'individual') return;
  if (state.arrestActive) {
    deactivateArrest();
  } else {
    activateArrest();
  }
}

/** Toggle audio mute on/off. */
function toggleMute() {
  state.muted = !state.muted;
  document.getElementById('btn-mute').textContent = state.muted ? 'Unmute' : 'Mute';
}

/**
 * Silence the alarm for 120 seconds (standard clinical silence duration).
 * A countdown updates the button text every second. After 120s, the alarm
 * automatically re-enables.
 */
function silenceAlarm() {
  state.alarmSilenced = true;
  state.silenceRemaining = 120;
  document.getElementById('btn-silence').textContent = 'Silenciado (' + state.silenceRemaining + 's)';
  clearInterval(state.silenceInterval);
  state.silenceInterval = setInterval(() => {
    state.silenceRemaining--;
    if (state.silenceRemaining <= 0) {
      clearInterval(state.silenceInterval);
      state.alarmSilenced = false;
      document.getElementById('btn-silence').textContent = 'Silenciar Alarma';
    } else {
      document.getElementById('btn-silence').textContent = 'Silenciado (' + state.silenceRemaining + 's)';
    }
  }, 1000);
}

/**
 * Wire up all DOM event listeners. Called once from main.js when the module
 * loads. Safe to call after DOM is parsed (modules are deferred by default).
 */
export function initEventListeners() {
  // --- Landing page ---
  document.getElementById('btn-create-session').addEventListener('click', createSession);
  document.getElementById('btn-join-session').addEventListener('click', () => {
    document.getElementById('join-error').style.display = 'none';
    document.getElementById('join-code-input').value = '';
    showScreen('join-dialog');
  });
  document.getElementById('btn-individual').addEventListener('click', () => {
    showScreen('config-screen');
  });

  // --- Join dialog ---
  document.getElementById('btn-join-confirm').addEventListener('click', joinSession);
  document.getElementById('btn-join-back').addEventListener('click', () => showScreen('landing-screen'));
  document.getElementById('join-code-input').addEventListener('keydown', (e) => {
    if (e.key === 'Enter') joinSession();
  });

  // --- Config screen (individual mode) ---
  document.getElementById('btn-start').addEventListener('click', startIndividual);
  document.getElementById('btn-config-back').addEventListener('click', () => showScreen('landing-screen'));

  // --- Monitor toolbar ---
  document.getElementById('btn-back').addEventListener('click', returnFromMonitor);
  document.getElementById('btn-arrest').addEventListener('click', toggleArrest);
  document.getElementById('btn-defib').addEventListener('click', triggerDefibrillation);
  document.getElementById('btn-mute').addEventListener('click', toggleMute);
  document.getElementById('btn-silence').addEventListener('click', silenceAlarm);

  // --- Professor sidebar ---
  document.getElementById('btn-apply').addEventListener('click', professorApplyChanges);
  document.getElementById('btn-sidebar-arrest').addEventListener('click', professorToggleArrest);
  document.getElementById('btn-sidebar-defib').addEventListener('click', professorDefibrillate);
  document.getElementById('btn-close-session').addEventListener('click', () => {
    if (confirm('¿Cerrar la sesión? Los alumnos serán desconectados.')) {
      stopAndCleanup();
      closeSession();
      showScreen('landing-screen');
    }
  });

  // --- Session ended overlay ---
  document.getElementById('btn-session-ended-back').addEventListener('click', () => {
    document.getElementById('session-ended-overlay').style.display = 'none';
    showScreen('landing-screen');
  });
}
