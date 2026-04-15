/**
 * main.js — Application entry point.
 *
 * This is the only file loaded by index.html (<script type="module">).
 * ES modules resolve the full dependency graph automatically:
 *   main.js → ui.js → monitor.js, session.js → signals.js, audio.js, canvas.js
 *                                              → config.js, state.js
 *
 * Modules are deferred by default, so the DOM is fully parsed when this runs.
 *
 * Signal recording data (data/signals.json) is preloaded at startup so it's
 * available immediately when the user selects a recording-based rhythm. The
 * fetch runs in the background while the user is on the landing/config screen,
 * so there's no visible delay.
 */

import { initEventListeners } from './ui.js';
import { loadSignalData } from './signals.js';
import { USE_RECORDINGS } from './config.js';

initEventListeners();

// Preload signal recordings in the background (non-blocking).
// By the time the user navigates to the monitor, the data is cached.
if (USE_RECORDINGS) {
  loadSignalData().catch(err => {
    console.warn('Could not preload signal data:', err.message);
    // Non-fatal: synthetic fallback will be used
  });
}
