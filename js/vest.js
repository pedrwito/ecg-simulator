/**
 * vest.js — Web Serial link to the Sinuplexor vest controller.
 *
 * This is the ONLY file that touches navigator.serial, deliberately mirroring
 * the rule that session.js is the only file that touches Supabase. To move to
 * Electron/Tauri (node-serialport) or a localhost bridge daemon later, you
 * rewrite this file and nothing else changes.
 *
 * Wire protocol (see firmware/Sinuplexor4/Sinuplexor4.ino):
 *
 *   V,<ok>,<mask>,<count>     sent on every state change + once per second
 *     ok    1 = all required electrodes seated (debounced by the firmware)
 *     mask  10-bit bitmap, bit i set = electrode i reads correct
 *     count number of required electrodes currently correct
 *
 *   Lines starting with '#' are informational banners. Any line that does not
 *   start with "V," is ignored, so a stray byte at connect time can never be
 *   mistaken for a state.
 *
 * Design decisions:
 * - Three distinct states, not two. "No port" (hardware never connected),
 *   "port open but silent" (board unplugged or hung), and "port open, ok=0"
 *   (vest plugged in, electrodes not seated) mean different things to a
 *   student and must not collapse into one "disconnected".
 * - state.vestRequired turns on only when a vest actually connects. Without
 *   hardware the app behaves exactly as it did before this module existed,
 *   which is what lets one deploy serve a class where only some students have
 *   a vest.
 * - Vest state is deliberately LOCAL. It is never written to the sessions
 *   table — a student unplugging their vest must not disturb anyone else, and
 *   must not fight the professor's arrest state. The professor sees vest
 *   status through Presence instead (ephemeral per-connection data), which
 *   needs no schema change.
 * - The firmware already debounces, so this module trusts the `ok` flag and
 *   does no smoothing of its own.
 */

import state from './state.js';
import {
  VEST_BAUD, VEST_STALE_MS, VEST_GRACE_MS, VEST_WATCHDOG_MS,
  VEST_ELECTRODES, VEST_IGNORED_MASK,
} from './config.js';
import { publishVestPresence } from './session.js';

/** Set while tearing down, so the read loop exits instead of retrying. */
let _readAbort = false;

// ─── Connection ────────────────────────────────────────────────────────────

/** @returns {boolean} true if this browser implements Web Serial. */
export function isVestSupported() {
  return 'serial' in navigator;
}

/**
 * Connect to a vest. Must be called from a user gesture (click) — the browser
 * requires one before it will show the port picker.
 *
 * No `filters` are passed to requestPort() on purpose: filtering by USB vendor
 * ID would hide any board whose USB-serial chip we did not anticipate, leaving
 * the student unable to select it at all. Showing every port is more robust.
 */
export async function connectVest() {
  if (!state.vestSupported) return;
  try {
    const port = await navigator.serial.requestPort();
    await openPort(port);
  } catch (err) {
    // NotFoundError = the user dismissed the picker without choosing. Not an error.
    if (err.name === 'NotFoundError') return;
    console.warn('[vest] connect failed:', err);
    alert('No se pudo conectar el chaleco: ' + err.message +
          '\n\nVerificá que el Monitor Serie del IDE de Arduino esté cerrado — ' +
          'solo un programa puede usar el puerto a la vez.');
  }
}

/**
 * Reconnect silently to a port this origin was already granted.
 * Permission survives reloads, so returning students never see the picker
 * again. No user gesture is needed here: only requestPort() requires one.
 */
export async function autoConnectVest() {
  if (!state.vestSupported || state.vestPort) return;
  try {
    const ports = await navigator.serial.getPorts();
    if (ports.length === 0) return;
    await openPort(ports[0]);
  } catch (err) {
    console.warn('[vest] auto-connect failed:', err.message);
  }
}

/** Open a port and start reading. Shared by connectVest and autoConnectVest. */
async function openPort(port) {
  await port.open({ baudRate: VEST_BAUD });

  state.vestPort = port;
  state.vestRequired = true;
  state.vestConnected = false;
  state.vestOk = false;
  state.vestMask = 0;
  state.vestLastLineAt = Date.now();

  // Opening a port asserts DTR, which resets Uno/Nano-class boards into their
  // bootloader for ~2s. Hold the watchdog off until the sketch is running
  // again, or we would declare the board dead the instant we connected.
  state.vestGraceUntil = Date.now() + VEST_GRACE_MS;

  _readAbort = false;
  refreshVestUI();
  startWatchdog();
  readLoop(port);   // intentionally not awaited — runs until disconnect
}

/**
 * Disconnect and release the port. Sets vestRequired = false, so the monitor
 * returns to normal (non-gated) operation rather than sitting at LEAD OFF.
 */
export async function disconnectVest() {
  _readAbort = true;

  clearInterval(state.vestWatchdog);
  state.vestWatchdog = null;

  if (state.vestReader) {
    try { await state.vestReader.cancel(); } catch { /* already gone */ }
  }
  if (state.vestPort) {
    try { await state.vestPort.close(); } catch { /* already gone */ }
  }

  state.vestPort = null;
  state.vestRequired = false;
  state.vestConnected = false;
  state.vestOk = false;
  state.vestMask = 0;
  refreshVestUI();
}

/** Toggle used by the toolbar button. */
export function toggleVest() {
  return state.vestPort ? disconnectVest() : connectVest();
}

// ─── Reading ───────────────────────────────────────────────────────────────

/**
 * Read newline-delimited lines from the port until the port closes or
 * disconnectVest() aborts. Chunks arrive at arbitrary boundaries, so partial
 * lines are buffered across reads.
 */
async function readLoop(port) {
  const decoder = new TextDecoder();
  let buf = '';

  while (port.readable && !_readAbort) {
    const reader = port.readable.getReader();
    state.vestReader = reader;

    try {
      while (true) {
        const { value, done } = await reader.read();
        if (done) break;

        buf += decoder.decode(value, { stream: true });

        let nl;
        while ((nl = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, nl).replace(/\r$/, '').trim();
          buf = buf.slice(nl + 1);
          if (line) handleLine(line);
        }

        // A device spewing without newlines must not grow this forever.
        if (buf.length > 512) buf = '';
      }
    } catch (err) {
      // Typically the USB cable was pulled mid-read.
      if (!_readAbort) console.warn('[vest] read error:', err.message);
    } finally {
      try { reader.releaseLock(); } catch { /* already released */ }
      state.vestReader = null;
    }
  }
}

/** Parse one line of the wire protocol. Unknown lines are ignored. */
function handleLine(line) {
  if (!line.startsWith('V,')) return;      // '#' banners and noise

  const parts = line.split(',');
  if (parts.length < 3) return;

  const mask = parseInt(parts[2], 10);
  if (!Number.isFinite(mask)) return;

  state.vestLastLineAt = Date.now();
  state.vestConnected = true;
  state.vestOk = (parts[1] === '1');       // firmware already debounced this
  state.vestMask = mask;

  refreshVestUI();
}

// ─── Watchdog ──────────────────────────────────────────────────────────────

/**
 * The board sends a heartbeat once per second. If lines stop arriving the
 * board was unplugged, reset, or hung — treat that as "not connected" without
 * closing the port, so it recovers on its own when the board comes back.
 */
function startWatchdog() {
  clearInterval(state.vestWatchdog);
  state.vestWatchdog = setInterval(() => {
    if (!state.vestPort) return;
    const now = Date.now();
    if (now < state.vestGraceUntil) return;

    if (now - state.vestLastLineAt > VEST_STALE_MS && state.vestConnected) {
      state.vestConnected = false;
      state.vestOk = false;
      refreshVestUI();
    }
  }, VEST_WATCHDOG_MS);
}

// ─── UI ────────────────────────────────────────────────────────────────────

/**
 * Names of required electrodes that are currently not seated.
 * Electrodes in VEST_IGNORED_MASK (C1, whose cable is broken on the current
 * hardware) are excluded, matching the firmware's pass/fail gate.
 *
 * @returns {string[]} e.g. ['C3', 'L']
 */
export function missingElectrodes() {
  const out = [];
  for (let i = 0; i < VEST_ELECTRODES.length; i++) {
    if (VEST_IGNORED_MASK & (1 << i)) continue;
    if (!(state.vestMask & (1 << i))) out.push(VEST_ELECTRODES[i]);
  }
  return out;
}

/** Update the toolbar button, the status pill, and the LEAD OFF banner detail. */
function refreshVestUI() {
  const btn    = document.getElementById('btn-vest');
  const pill   = document.getElementById('vest-status');
  const detail = document.getElementById('vest-banner-detail');

  if (btn) {
    btn.textContent = state.vestPort ? 'Desconectar Chaleco' : 'Conectar Chaleco';
    btn.classList.toggle('active', !!state.vestPort);
  }

  let cls = 'vest-off';
  let text = 'Sin chaleco';
  let detailText = 'Conectá el chaleco para ver la señal.';

  if (state.vestPort && !state.vestConnected) {
    cls = 'vest-bad';
    text = 'Chaleco sin señal';
    detailText = 'Sin datos del controlador — revisá el cable USB.';
  } else if (state.vestPort && !state.vestOk) {
    const miss = missingElectrodes();
    cls = 'vest-bad';
    text = miss.length ? 'Electrodos: ' + miss.join(' ') : 'Electrodos incompletos';
    detailText = miss.length
      ? 'Revisá los electrodos: ' + miss.join(', ')
      : 'Electrodos incompletos.';
  } else if (state.vestPort) {
    cls = 'vest-ok';
    text = 'Chaleco OK';
    detailText = '';
  }

  if (pill) {
    pill.className = 'vest-status ' + cls;
    pill.textContent = text;
  }
  if (detail) detail.textContent = detailText;

  publishVestPresence();
}

// ─── Init ──────────────────────────────────────────────────────────────────

/**
 * Feature-detect Web Serial, wire USB plug/unplug events, and try to
 * reconnect to a previously-authorized port.
 *
 * On browsers without Web Serial (Firefox, Safari, anything mobile) the vest
 * controls are hidden and the app runs exactly as it did before.
 */
export function initVest() {
  state.vestSupported = isVestSupported();

  const btn  = document.getElementById('btn-vest');
  const pill = document.getElementById('vest-status');

  if (!state.vestSupported) {
    if (btn) btn.style.display = 'none';
    if (pill) pill.style.display = 'none';
    return;
  }

  // Physical USB plug/unplug of an already-authorized board.
  navigator.serial.addEventListener('connect', () => {
    if (!state.vestPort) autoConnectVest();
  });
  navigator.serial.addEventListener('disconnect', (e) => {
    if (state.vestPort && e.target === state.vestPort) disconnectVest();
  });

  refreshVestUI();
  autoConnectVest();
}
