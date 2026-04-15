/**
 * session.js — Supabase session management (the ONLY file that touches Supabase).
 *
 * This module handles all real-time multi-user functionality:
 * - Professor creates a session → gets a 6-character code
 * - Students join with the code → their monitors sync in real-time
 * - Professor changes parameters → pushed to Supabase → students receive via
 *   postgres_changes realtime subscription
 * - Presence tracking shows how many students are connected
 *
 * Design decisions:
 * - This is intentionally the ONLY file that references window.supabase or the
 *   Supabase SDK. To swap Supabase for a custom WebSocket/REST backend later,
 *   you only rewrite this file — nothing else changes.
 * - Session data is stored in a `sessions` table with columns matching the
 *   parameter names (hr, spo2, rr, etco2, nibp_sys, nibp_dia, temp, rhythm,
 *   arrest, code). RLS policies should restrict writes to the session creator.
 * - DEFAULTS from config.js are used for initial session values and as fallbacks
 *   when parsing session data, eliminating hardcoded magic numbers.
 */

import state from './state.js';
import { SUPABASE_URL, SUPABASE_ANON_KEY, DEFAULTS } from './config.js';
import { startMonitor, applyParameters, activateArrest, deactivateArrest, stopAndCleanup } from './monitor.js';

/**
 * Initialize the Supabase client. Returns false if credentials are placeholder
 * values (app can still run in individual mode without Supabase).
 * @returns {boolean} true if initialization succeeded
 */
export function initSupabase() {
  if (SUPABASE_URL === 'YOUR_SUPABASE_URL' || SUPABASE_ANON_KEY === 'YOUR_SUPABASE_ANON_KEY') {
    return false;
  }
  state.sbClient = window.supabase.createClient(SUPABASE_URL, SUPABASE_ANON_KEY);
  return true;
}

/**
 * Generate a random 6-character session code.
 * Uses only unambiguous characters (no 0/O, 1/I/L) to avoid confusion when
 * students type codes manually.
 * @returns {string} 6-character uppercase alphanumeric code
 */
function generateCode() {
  const chars = 'ABCDEFGHJKLMNPQRSTUVWXYZ23456789';
  let code = '';
  for (let i = 0; i < 6; i++) code += chars[Math.floor(Math.random() * chars.length)];
  return code;
}

/**
 * Create a new session (professor flow).
 * Inserts a row in the `sessions` table with default parameters, sets session
 * state, populates the professor sidebar inputs, and starts the monitor.
 */
export async function createSession() {
  if (!initSupabase()) {
    alert('Error: Configura las credenciales de Supabase en el código (SUPABASE_URL y SUPABASE_ANON_KEY).');
    return;
  }

  const code = generateCode();

  const { error } = await state.sbClient.from('sessions').insert({
    code: code,
    hr: DEFAULTS.hr,
    spo2: DEFAULTS.spo2,
    rr: DEFAULTS.rr,
    etco2: DEFAULTS.etco2,
    nibp_sys: DEFAULTS.sys,
    nibp_dia: DEFAULTS.dia,
    temp: DEFAULTS.temp,
    rhythm: DEFAULTS.rhythm,
    arrest: false,
  });

  if (error) {
    alert('Error al crear sesión: ' + error.message);
    return;
  }

  state.sessionMode = 'professor';
  state.sessionCode = code;

  // Initialize CFG from defaults
  Object.assign(state.CFG, {
    rhythm: DEFAULTS.rhythm,
    lead: DEFAULTS.lead,
    hr: DEFAULTS.hr,
    spo2: DEFAULTS.spo2,
    rr: DEFAULTS.rr,
    etco2: DEFAULTS.etco2,
    sys: DEFAULTS.sys,
    dia: DEFAULTS.dia,
    temp: DEFAULTS.temp,
  });

  // Populate professor sidebar inputs to match
  document.getElementById('sb-rhythm').value = state.CFG.rhythm;
  document.getElementById('sb-hr').value = state.CFG.hr;
  document.getElementById('sb-spo2').value = state.CFG.spo2;
  document.getElementById('sb-rr').value = state.CFG.rr;
  document.getElementById('sb-etco2').value = state.CFG.etco2;
  document.getElementById('sb-sys').value = state.CFG.sys;
  document.getElementById('sb-dia').value = state.CFG.dia;
  document.getElementById('sb-temp').value = state.CFG.temp;

  await startMonitor();
  setupPresence();
}

/**
 * Join an existing session (student flow).
 * Looks up the session by code, loads its current parameters, starts the
 * monitor, and subscribes to realtime updates.
 */
export async function joinSession() {
  if (!initSupabase()) {
    alert('Error: Configura las credenciales de Supabase en el código (SUPABASE_URL y SUPABASE_ANON_KEY).');
    return;
  }

  const code = document.getElementById('join-code-input').value.trim().toUpperCase();
  if (code.length !== 6) {
    document.getElementById('join-error').textContent = 'El código debe tener 6 caracteres';
    document.getElementById('join-error').style.display = 'block';
    return;
  }

  const { data, error } = await state.sbClient.from('sessions').select('*').eq('code', code).single();

  if (error || !data) {
    document.getElementById('join-error').textContent = 'Sesión no encontrada';
    document.getElementById('join-error').style.display = 'block';
    return;
  }

  state.sessionMode = 'student';
  state.sessionCode = code;

  // Load parameters from the session row (fall back to DEFAULTS for missing fields)
  state.CFG.rhythm = data.rhythm || DEFAULTS.rhythm;
  state.CFG.lead = DEFAULTS.lead;
  state.CFG.hr = data.hr || DEFAULTS.hr;
  state.CFG.spo2 = data.spo2 || DEFAULTS.spo2;
  state.CFG.rr = data.rr || DEFAULTS.rr;
  state.CFG.etco2 = data.etco2 || DEFAULTS.etco2;
  state.CFG.sys = data.nibp_sys || DEFAULTS.sys;
  state.CFG.dia = data.nibp_dia || DEFAULTS.dia;
  state.CFG.temp = data.temp || DEFAULTS.temp;

  await startMonitor();
  subscribeToSession();
  setupPresence();
}

/**
 * Subscribe to realtime changes on the session row.
 * - UPDATE events: professor changed parameters → apply them locally
 * - DELETE events: professor closed the session → show "session ended" overlay
 */
function subscribeToSession() {
  state.realtimeChannel = state.sbClient
    .channel('session-' + state.sessionCode)
    .on('postgres_changes',
      { event: 'UPDATE', schema: 'public', table: 'sessions', filter: 'code=eq.' + state.sessionCode },
      async (payload) => {
        const newData = payload.new;
        await applyParameters({
          hr: newData.hr,
          spo2: newData.spo2,
          rr: newData.rr,
          etco2: newData.etco2,
          nibp_sys: newData.nibp_sys,
          nibp_dia: newData.nibp_dia,
          temp: newData.temp,
          rhythm: newData.rhythm,
          arrest: newData.arrest,
        });
      }
    )
    .on('postgres_changes',
      { event: 'DELETE', schema: 'public', table: 'sessions', filter: 'code=eq.' + state.sessionCode },
      () => {
        handleSessionEnded();
      }
    )
    .subscribe();
}

/**
 * Set up Supabase Presence to track connected users.
 * Each user tracks their role ('professor' or 'student'). The professor's UI
 * shows the count of connected students.
 */
function setupPresence() {
  state.presenceChannel = state.sbClient.channel('presence-' + state.sessionCode, {
    config: { presence: { key: state.sessionMode + '-' + Math.random().toString(36).slice(2, 8) } }
  });

  state.presenceChannel.on('presence', { event: 'sync' }, () => {
    const presenceState = state.presenceChannel.presenceState();
    let count = 0;
    for (const key in presenceState) {
      for (const p of presenceState[key]) {
        if (p.role === 'student') count++;
      }
    }
    state.studentCount = count;
    const el = document.getElementById('display-student-count');
    if (el) el.textContent = 'Alumnos conectados: ' + state.studentCount;
  });

  state.presenceChannel.subscribe(async (status) => {
    if (status === 'SUBSCRIBED') {
      await state.presenceChannel.track({ role: state.sessionMode });
    }
  });
}

/**
 * Handle session ended (student side).
 * Called when the professor deletes the session row. Stops the monitor and
 * shows the "session ended" overlay.
 */
export function handleSessionEnded() {
  stopAndCleanup();
  cleanupSession();
  document.getElementById('session-ended-overlay').style.display = 'flex';
}

/**
 * Close the session (professor side).
 * Deletes the session row from Supabase, which triggers DELETE events for
 * all subscribed students.
 */
export async function closeSession() {
  if (!state.sbClient || !state.sessionCode) return;
  await state.sbClient.from('sessions').delete().eq('code', state.sessionCode);
  cleanupSession();
}

/**
 * Clean up Supabase channels and reset session state.
 * Called by both professor (after closing) and student (after leaving/ended).
 */
export function cleanupSession() {
  if (state.realtimeChannel) {
    state.sbClient.removeChannel(state.realtimeChannel);
    state.realtimeChannel = null;
  }
  if (state.presenceChannel) {
    state.sbClient.removeChannel(state.presenceChannel);
    state.presenceChannel = null;
  }
  state.sessionCode = null;
  state.sessionMode = null;
  state.studentCount = 0;
}

/**
 * Professor: read sidebar inputs, apply changes locally, and push to Supabase.
 * Falls back to DEFAULTS if input parsing fails (e.g. empty field).
 */
export async function professorApplyChanges() {
  const newParams = {
    rhythm: document.getElementById('sb-rhythm').value,
    hr:     parseInt(document.getElementById('sb-hr').value) || DEFAULTS.hr,
    spo2:   parseInt(document.getElementById('sb-spo2').value) || DEFAULTS.spo2,
    rr:     parseInt(document.getElementById('sb-rr').value) || DEFAULTS.rr,
    etco2:  parseInt(document.getElementById('sb-etco2').value) || DEFAULTS.etco2,
    nibp_sys: parseInt(document.getElementById('sb-sys').value) || DEFAULTS.sys,
    nibp_dia: parseInt(document.getElementById('sb-dia').value) || DEFAULTS.dia,
    temp:   parseFloat(document.getElementById('sb-temp').value) || DEFAULTS.temp,
    arrest: state.arrestActive,
  };

  // Apply locally (this also clamps HR for the rhythm via applyParameters → regenerateSignals)
  await applyParameters(newParams);

  // Reflect any HR adjustment back in the sidebar input
  document.getElementById('sb-hr').value = state.CFG.hr;

  // Push updated state to Supabase (triggers realtime update for students)
  if (state.sbClient && state.sessionCode) {
    await state.sbClient.from('sessions').update({
      hr: state.CFG.hr,
      spo2: state.CFG.spo2,
      rr: state.CFG.rr,
      etco2: state.CFG.etco2,
      nibp_sys: state.CFG.sys,
      nibp_dia: state.CFG.dia,
      temp: state.CFG.temp,
      rhythm: state.CFG.rhythm,
      arrest: state.arrestActive,
    }).eq('code', state.sessionCode);
  }
}

/**
 * Professor: toggle cardiac arrest and push the state to Supabase.
 */
export async function professorToggleArrest() {
  if (state.arrestActive) {
    deactivateArrest();
  } else {
    activateArrest();
  }

  if (state.sbClient && state.sessionCode) {
    await state.sbClient.from('sessions').update({
      arrest: state.arrestActive,
    }).eq('code', state.sessionCode);
  }
}
