/**
 * state.js — Single shared mutable state object.
 *
 * Every file that needs runtime state imports this same object by reference.
 * Mutations from any module are visible to all others immediately.
 *
 * Design decisions:
 * - One object instead of scattered global variables makes it clear what state
 *   exists and who can modify it. When debugging, you can inspect `state` in
 *   the console to see the full application state.
 * - Buffers and signals start as null and are initialized in startMonitor()
 *   because their size depends on runtime constants from config.js.
 * - Timer IDs are tracked here so stopAndCleanup() can reliably clear them
 *   all from one place, preventing audio/interval leaks on screen transitions.
 */

const state = {
  // --- Runtime parameters ---
  // Populated from DEFAULTS, user input, or Supabase session data.
  // Contains: rhythm, lead, hr, spo2, rr, etco2, sys, dia, temp
  CFG: {},

  // --- Runtime flags ---
  running: false,            // true while the animation loop is active
  arrestActive: false,       // true during cardiac arrest simulation
  muted: false,              // true when all audio is muted
  alarmSilenced: false,      // true during the 120s alarm silence period
  silenceRemaining: 0,       // seconds left in silence countdown
  spo2Decay: 0,              // current SpO2 during arrest (decays from CFG.spo2 toward 0)

  // --- Session ---
  sessionMode: null,         // 'professor' | 'student' | 'individual'
  sessionCode: null,         // 6-character session code (e.g. "A3BK7R")
  sbClient: null,            // Supabase client instance
  realtimeChannel: null,     // Supabase realtime channel for postgres_changes
  presenceChannel: null,     // Supabase presence channel for student count
  studentCount: 0,           // number of students currently connected

  // --- Audio ---
  audioCtx: null,            // Web Audio API AudioContext (created on first user gesture)

  // --- Waveform circular buffers ---
  // Float32Array buffers that the main loop writes to and the canvas reads from.
  // ECG and PPG share the same write position (5s window, 750 samples).
  // RESP and CO2 share theirs (20s window, 3000 samples).
  ecgBuf: null,
  ppgBuf: null,
  respBuf: null,
  co2Buf: null,
  ecgWritePos: 0,            // current write index into ECG/PPG buffers
  respWritePos: 0,           // current write index into RESP/CO2 buffers
  ecgSampleIdx: 0,           // current read index into ECG/PPG pre-generated arrays
  respSampleIdx: 0,          // current read index into RESP/CO2 pre-generated arrays (independent so rhythm changes don't restart resp)

  // --- Pre-generated full signals ---
  // 60 seconds of signal data generated upfront. The main loop reads from these
  // cyclically (sampleIdx wraps around). Regenerated when parameters change.
  ecgFull: null,
  ppgFull: null,
  respFull: null,
  co2Full: null,

  // --- R-peak detection ---
  // Simple threshold crossing detector: beep when ECG rises past ECG_THRESH
  // and at least REFRACTORY samples have passed since the last beep.
  prevEcg: 0,                // previous ECG sample value (for crossing detection)
  samplesSinceBeep: 999,     // samples since last heartbeat beep (starts high to allow first beep)

  // --- Timing ---
  lastFrameTime: 0,          // timestamp of previous animation frame (ms)
  sampleAccum: 0,            // fractional sample accumulator for frame-rate-independent playback

  // --- Timer/animation IDs ---
  // Stored so they can be reliably cleared in stopAndCleanup().
  animFrameId: null,          // requestAnimationFrame ID
  silenceInterval: null,      // setInterval ID for silence countdown
  spo2DecayInterval: null,    // setInterval ID for SpO2 decay during arrest
  alarmFlashInterval: null,   // setInterval ID for alarm indicator flashing
  alarmFlashOn: false,        // toggle state for alarm flash animation
};

export default state;
