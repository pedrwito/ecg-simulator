/**
 * ecg.js — Electrocardiograph module: 12-lead ECG drawn on millimetre paper.
 *
 * Deliberately separate from the bedside monitor (monitor.js). A monitor shows
 * one lead sweeping on a black screen next to the vital signs; an
 * electrocardiograph shows up to 12 leads at a calibrated scale, so intervals
 * and amplitudes can be read by counting squares. That calibration is the whole
 * point of this module:
 *
 *   horizontal  25 mm/s  → 1 small square = 40 ms, 1 large square = 200 ms
 *   vertical    10 mm/mV → 1 small square = 0.1 mV, 2 large squares = 1 mV
 *
 * Design decisions:
 * - Recordings only, never the synthetic Gaussian model. The synthetic ECG has
 *   one beat shape for every lead, which is fine on a monitor and wrong on a
 *   12-lead page. Rhythms the monitor synthesizes (sinus, tachy, brady) point
 *   at a real recording through `ecgKey` in the RHYTHMS registry.
 * - Amplitude comes from the recording's `mv` scale (see
 *   scripts/preprocess_signals.py). The monitor normalizes every lead to
 *   [-1, 1]; doing that here would make a 0.2 mV lead III as tall as a 2 mV V4.
 * - A recording with a single real lead (VFib from a defibrillator channel) is
 *   shown as a single strip. Copying it into 12 labelled slots would teach
 *   something false.
 * - The screens follow a clinical cardiograph (the layout and workflow of the
 *   Philips PageWriter TC, per its Instructions for Use), so what students
 *   learn here transfers to a real device:
 *     Main screen — live leads, every lead sweeping at the same instant. A lead
 *       with a loose electrode is a red dotted line. Paper-style by default,
 *       green on black as an option: real devices ship both (the Biocare iE 6
 *       calls them "Classic White" and "Classic Black").
 *     Map — a body diagram marking which electrodes are loose.
 *     Freeze — stops the sweep and pages back through the last 5 minutes
 *       (the Biocare iE 6's Freeze key), so a passing event can be examined
 *       and printed.
 *     ECG button — lit when every lead has a signal; captures 10 seconds.
 *     Preview — the page exactly as it prints: pink paper, columns that are
 *       consecutive time segments, and a full-length rhythm strip under 3×4.
 *   The device's automatic interpretation is deliberately left out: reading
 *   the trace is the student's job.
 * - With a vest connected, each lead is gated on the electrodes it is derived
 *   from, so one loose electrode flattens exactly the leads it would on a real
 *   patient. The monitor gates all-or-nothing; here per-lead is the lesson.
 * - The page is laid out in millimetres and scaled to the canvas width, so the
 *   print stylesheet can output it at true size.
 */

import state from './state.js';
import { FS, RHYTHMS, VEST_ELECTRODES, VEST_IGNORED_MASK } from './config.js';
import { loadSignalData, getRecordingSet } from './signals.js';
import { toggleVest } from './vest.js';

// ─── Page geometry (millimetres) ───────────────────────────────────────────

const LEADS = ['I', 'II', 'III', 'aVR', 'aVL', 'aVF', 'V1', 'V2', 'V3', 'V4', 'V5', 'V6'];

/** Trace width. 250 mm is 10 s at 25 mm/s — the standard ECG page. */
const PAPER_MM = 250;
/** Left gutter holding the 1 mV calibration pulse. */
const GUTTER_MM = 13;
const RIGHT_MM = 3;
const TOP_MM = 2;
/** Bottom band for the speed / gain / heart-rate line. */
const BOTTOM_MM = 7;
const PAGE_MM = GUTTER_MM + PAPER_MM + RIGHT_MM;

/** Column layouts. `strip` adds a full-width rhythm strip of the chosen lead
 *  to the printed page (the live screen shows the 12 leads only). */
const FORMATS = {
  '4x3':  { cols: [['I', 'II', 'III'], ['aVR', 'aVL', 'aVF'], ['V1', 'V2', 'V3'], ['V4', 'V5', 'V6']], strip: true },
  '6x2':  { cols: [LEADS.slice(0, 6), LEADS.slice(6)] },
  '12x1': { cols: [LEADS] },
  '1':    { cols: null },   // one lead, chosen by the user
};

// ─── Lead → electrode dependencies (vest gating) ───────────────────────────
// Bipolar limb leads need their two electrodes. Augmented leads need all three
// limb electrodes. Each precordial lead needs its chest electrode plus the
// three limb electrodes, because it is measured against Wilson's central
// terminal. Every lead needs the neutral (right leg) electrode.

const LIMB = ['R', 'L', 'F'];
const LEAD_ELECTRODES = {
  I: ['R', 'L'], II: ['R', 'F'], III: ['L', 'F'],
  aVR: LIMB, aVL: LIMB, aVF: LIMB,
  V1: ['C1', ...LIMB], V2: ['C2', ...LIMB], V3: ['C3', ...LIMB],
  V4: ['C4', ...LIMB], V5: ['C5', ...LIMB], V6: ['C6', ...LIMB],
};

/** Electrode positions on the map, in the SVG's 200×240 box. The patient
 *  faces the viewer, so their right arm (R) is on the left of the drawing. */
const ELECTRODE_XY = {
  R: [42, 62], L: [158, 62], N: [70, 218], F: [130, 218],
  C1: [91, 104], C2: [109, 104], C3: [119, 114],
  C4: [129, 125], C5: [143, 127], C6: [157, 128],
};

/** The two display styles share geometry and differ only in colour. The
 *  preview always uses PAPER, because that is what comes out of the printer. */
const SCREEN = {
  background: '#000', minor: null, major: '#2C2C2C',
  trace: '#00E000', label: '#E6E6E6', off: '#FF3B30', text: '#9A9A9A',
};
const PAPER = {
  background: '#FFF7F5', minor: '#F7CFD2', major: '#E79AA1',
  trace: '#111', label: '#111', off: '#C62828', text: '#111',
};

/**
 * Whether one vest electrode is seated. Without a vest every electrode counts
 * as seated, so the module works with no hardware at all.
 */
function electrodeSeated(name) {
  if (!state.vestRequired) return true;
  if (!state.vestConnected) return false;
  const mask = state.vestMask | VEST_IGNORED_MASK;
  return !!(mask & (1 << VEST_ELECTRODES.indexOf(name)));
}

/**
 * Leads that currently have no signal because of the vest.
 * Empty when no vest is in use, so the module works without hardware.
 * @returns {Set<string>}
 */
function leadsOff() {
  const off = new Set();
  if (!state.vestRequired) return off;
  if (!electrodeSeated('N')) return new Set(LEADS);

  for (const lead of LEADS) {
    if (!LEAD_ELECTRODES[lead].every(electrodeSeated)) off.add(lead);
  }
  return off;
}

/** Freeze can look back this far, as on the Biocare iE 6. */
const HISTORY_SECONDS = 300;

// ─── View state ────────────────────────────────────────────────────────────

const view = {
  rhythm: null,
  example: 0,        // index among the available patients for this rhythm
  format: '6x2',
  lead: 'II',        // rhythm-strip lead (4×3) or the single lead ('1')
  speed: 25,         // mm/s
  gain: 10,          // mm/mV, limb leads
  chestGain: 10,     // mm/mV, precordial leads (halved by the 10/5 and 20/10 settings)
  style: 'paper',    // live screen: 'paper' or 'black'
  frozen: false,     // true = preview of the acquired page
  held: false,       // true = Freeze: main screen stopped on a past window
  pageStart: 0,      // first sample of the window shown while held
  winStart: 0,       // first sample of the acquired page
  offLog: [],        // [{ n, off }] — which leads lost contact, and from when
  rec: null,         // { patientId, count, nLeads, leads } from signals.js
  flat: false,       // asystole: no recording, every lead is a flat line
  t: 0,              // live playback position in samples (fractional)
  lastTs: 0,
  running: false,
  animId: null,
  dirty: true,       // acquired page needs a redraw
  grid: null,        // cached grid background
  gridKey: '',
  mapOpen: false,
  uiKey: '',         // last state pushed to the DOM (map, ECG button, HR)
};

// ─── Contact history ───────────────────────────────────────────────────────
// A frozen or acquired window lies in the past, so it has to show the contact
// each lead had THEN, not now: an electrode that fell off ten seconds ago must
// still be a gap in the trace after it is pressed back on. The log stores one
// entry per change, not per sample.

/** Record the current lead contact if it changed. Called while live. */
function logContact() {
  const off = leadsOff();
  const last = view.offLog[view.offLog.length - 1];
  const same = last && last.off.size === off.size && [...off].every(l => last.off.has(l));
  if (same) return;
  view.offLog.push({ n: Math.floor(view.t), off });

  // Drop entries that ended before the freeze history begins.
  const oldest = view.t - HISTORY_SECONDS * FS;
  while (view.offLog.length > 1 && view.offLog[1].n <= oldest) view.offLog.shift();
}

/** Whether a lead had lost contact at a given sample of the session. */
function wasOff(lead, sample) {
  for (let i = view.offLog.length - 1; i >= 0; i--) {
    if (view.offLog[i].n <= sample) return view.offLog[i].off.has(lead);
  }
  return false;
}

/** Recording length in samples (every recording is 10 s). */
function loopLength() {
  const ref = leadData('II');
  return ref ? ref.signal.length : 10 * FS;
}

/**
 * Gain for one lead. Precordial leads can run at half the limb gain: their
 * voltages are often several times larger, and the split setting is how a real
 * cardiograph keeps them from running into each other.
 */
function gainFor(lead) {
  return lead.startsWith('V') ? view.chestGain : view.gain;
}

/** Gain as printed on the page, e.g. "10" or "10/5". */
function gainLabel() {
  return view.gain === view.chestGain ? `${view.gain}` : `${view.gain}/${view.chestGain}`;
}

/** True when the recording cannot honestly fill a 12-lead layout. */
function isSingleLead() {
  return !!view.rec && view.rec.nLeads < 12;
}

/** Look up a lead; the original dataset spells augmented leads in upper case. */
function leadData(name) {
  if (!view.rec) return null;
  return view.rec.leads[name] || view.rec.leads[name.toUpperCase()] || null;
}

/** Heart rate from the recording's pre-computed R-peaks, or null. */
function heartRate() {
  const def = RHYTHMS[view.rhythm] || {};
  if (view.flat || def.noPulse) return null;
  const ld = leadData('II');
  if (!ld || ld.rPeaks.length < 2) return null;
  const peaks = ld.rPeaks;
  const seconds = (peaks[peaks.length - 1] - peaks[0]) / FS;
  return Math.round(60 * (peaks.length - 1) / seconds);
}

// ─── Layout ────────────────────────────────────────────────────────────────

/**
 * Resolve the current format into drawable cells.
 * `t0` is each cell's start time on the acquired page, where columns are
 * consecutive segments of one recording. Live cells ignore it.
 */
function buildCells() {
  const fmt = FORMATS[view.format];
  const single = isSingleLead();
  const cols = single ? [['II']] : (fmt.cols || [[view.lead]]);
  const strip = !single && !!fmt.strip && view.frozen;

  const colMm = PAPER_MM / cols.length;
  const rows = Math.max(...cols.map(c => c.length)) + (strip ? 1 : 0);
  const cells = [];

  cols.forEach((col, c) => {
    col.forEach((lead, r) => {
      cells.push({
        lead, row: r,
        label: single ? 'Derivación única' : lead,
        xMm: GUTTER_MM + c * colMm, wMm: colMm,
        t0: c * colMm / view.speed,
        boundary: c > 0,
      });
    });
  });
  if (strip) {
    cells.push({
      lead: view.lead, row: rows - 1, label: view.lead,
      xMm: GUTTER_MM, wMm: PAPER_MM, t0: 0, boundary: false,
    });
  }
  return { cells, rows };
}

// ─── Rendering ─────────────────────────────────────────────────────────────

/** Match the canvas bitmap to its CSS box at device resolution. */
function syncSize(canvas) {
  const dpr = window.devicePixelRatio || 1;
  const w = Math.round(canvas.clientWidth * dpr);
  const h = Math.round(canvas.clientHeight * dpr);
  if (canvas.width === w && canvas.height === h) return false;
  canvas.width = w;
  canvas.height = h;
  return true;
}

/**
 * Millimetre grid, rendered once per canvas size and blitted every frame.
 * Vertical lines are anchored where the traces start, so a trace begins
 * exactly on a heavy line.
 */
function gridLayer(W, H, ppm, theme) {
  const key = W + 'x' + H + theme.background;
  if (view.grid && view.gridKey === key) return view.grid;

  const grid = document.createElement('canvas');
  grid.width = W;
  grid.height = H;
  const g = grid.getContext('2d');
  g.fillStyle = theme.background;
  g.fillRect(0, 0, W, H);

  const x0 = GUTTER_MM * ppm;
  const rule = (everyMm, color) => {
    g.strokeStyle = color;
    g.lineWidth = 1;
    g.beginPath();
    const step = everyMm * ppm;
    for (let x = x0 % step; x <= W; x += step) {
      const px = Math.round(x) + 0.5;
      g.moveTo(px, 0); g.lineTo(px, H);
    }
    for (let y = 0; y <= H; y += step) {
      const py = Math.round(y) + 0.5;
      g.moveTo(0, py); g.lineTo(W, py);
    }
    g.stroke();
  };
  // Below ~3 px per millimetre the fine lines merge into a pink wash.
  if (theme.minor && ppm >= 3) rule(1, theme.minor);
  rule(5, theme.major);

  view.grid = grid;
  view.gridKey = key;
  return grid;
}

/**
 * Place each row's baseline according to how much room its traces need.
 *
 * Dividing the page into equal rows makes a tall lead (a 2.5 mV V3) run into
 * its neighbours while a small one (aVL) sits in empty space. Instead each row
 * is given height in proportion to what its leads actually reach above and
 * below the baseline — the "Automatic Position" setting of a real cardiograph
 * (Biocare iE 6). When the page is too short for everything, every row shrinks
 * by the same factor, so any remaining overlap is spread evenly.
 *
 * @returns {{ y0: number, base: number }[]} top edge and baseline of each row, in px
 */
function layoutRows(cells, rows, ppm, top, available) {
  const PAD_MM = 2;
  // Minimum reach: room above for the 1 mV calibration pulse and the lead
  // label, and a little below so a flat lead still gets a visible band.
  // (in millimetres, so rows mixing limb and chest gains compare correctly)
  const up = new Array(rows).fill(1.1 * view.gain);
  const down = new Array(rows).fill(0.3 * view.gain);

  for (const cell of cells) {
    const ld = view.flat ? null : leadData(cell.lead);
    if (!ld) continue;
    if (!ld.extent) {
      // Extent of the whole recording, not of the window on screen, so rows
      // do not shift as the sweep advances. Cached on the lead itself.
      let max = 0, min = 0;
      for (const v of ld.signal) { if (v > max) max = v; if (v < min) min = v; }
      const mv = ld.mv || 1;
      ld.extent = { up: max * mv, down: -min * mv };
    }
    const gain = gainFor(cell.lead);
    up[cell.row] = Math.max(up[cell.row], ld.extent.up * gain);
    down[cell.row] = Math.max(down[cell.row], ld.extent.down * gain);
  }

  const need = up.map((u, r) => u + down[r] + 2 * PAD_MM);
  const total = need.reduce((a, b) => a + b, 0) * ppm;
  // Too little room: shrink every row alike. Room to spare: keep the rows at
  // their true size and share the surplus out as space around them, so a
  // single strip sits in the middle of the page rather than stretched over it.
  const k = Math.min(1, available / total);
  const spare = Math.max(0, available - total) / rows;

  const out = [];
  let y = top;
  for (let r = 0; r < rows; r++) {
    out.push({ y0: y + spare / 2, base: y + spare / 2 + (up[r] + PAD_MM) * ppm * k });
    y += need[r] * ppm * k + spare;
  }
  return out;
}

/** The 1 mV reference pulse in the left gutter of a row. */
function drawCalibration(ctx, yBase, ppm) {
  const pulseMm = 0.2 * view.speed;              // 200 ms wide
  const xs = (GUTTER_MM - 1 - pulseMm) * ppm;
  const xe = xs + pulseMm * ppm;
  const top = yBase - view.gain * ppm;           // 1 mV at the limb gain
  const step = yBase - view.chestGain * ppm;     // 1 mV at the chest gain
  const mid = (xs + xe) / 2;
  ctx.beginPath();
  ctx.moveTo(xs - ppm, yBase);
  ctx.lineTo(xs, yBase);
  ctx.lineTo(xs, top);
  // With a split gain the pulse is stepped: first half limb, second half chest.
  ctx.lineTo(mid, top);
  ctx.lineTo(mid, step);
  ctx.lineTo(xe, step);
  ctx.lineTo(xe, yBase);
  ctx.lineTo(GUTTER_MM * ppm, yBase);
  ctx.stroke();
}

/**
 * Draw one lead into its cell.
 *
 * Every mode maps x positions to samples of the session timeline, which is
 * the recording looped from the moment the screen opened:
 *   Live     — a sweep window. Position i holds the most recent sample that
 *              landed there, so points left of the head are the current pass
 *              and points right of it the previous one, with a gap between.
 *   Freeze   — the window starting at pageStart, every lead at the same time.
 *   Preview  — the window starting at winStart + the column's own offset.
 * Samples where the lead had no contact are left out of the trace and drawn
 * as a red dotted baseline instead.
 */
function drawCell(ctx, cell, geo) {
  const { ppm, rowPos, H, theme } = geo;
  const x0 = cell.xMm * ppm;
  const w = cell.wMm * ppm;
  const yBase = rowPos[cell.row].base;
  const ld = view.flat ? null : leadData(cell.lead);
  const missing = !ld && !view.flat;

  const L = Math.max(2, Math.round(cell.wMm / view.speed * FS));
  const scale = view.flat ? 0 : ((ld && ld.mv) || 1) * gainFor(cell.lead) * ppm;
  const len = ld ? ld.signal.length : 1;
  const n = Math.floor(view.t);
  const head = n % L;
  const gap = Math.max(4, Math.round(L * 0.015));
  const live = !view.frozen && !view.held;
  const start = view.frozen ? view.winStart + Math.round(cell.t0 * FS) : view.pageStart;

  // Session sample shown at position i, or -1 for nothing.
  const sampleAt = i => {
    if (!live) return start + i;
    const src = i <= head ? n - head + i : n - head + i - L;
    return (src < 0 || (i > head && i - head <= gap)) ? -1 : src;
  };

  ctx.save();
  ctx.beginPath();
  ctx.rect(x0, 0, w, H);
  ctx.clip();

  const trace = new Path2D();
  const dropout = new Path2D();
  let pen = false;
  let offFrom = -1;
  let anyOff = false;
  for (let i = 0; i <= L; i++) {
    const src = i < L ? sampleAt(i) : -1;
    const x = x0 + (i / L) * w;
    const off = src >= 0 && (missing || wasOff(cell.lead, src));

    if (off) {
      if (offFrom < 0) offFrom = x;
      anyOff = anyOff || !missing;
    } else if (offFrom >= 0) {
      dropout.moveTo(offFrom, yBase);
      dropout.lineTo(x, yBase);
      offFrom = -1;
    }

    if (src < 0 || off) { pen = false; continue; }
    const y = yBase - (ld ? ld.signal[src % len] : 0) * scale;
    if (pen) trace.lineTo(x, y); else trace.moveTo(x, y);
    pen = true;
  }
  ctx.strokeStyle = theme.trace;
  ctx.stroke(trace);

  // Loose electrode: a red dotted line, as the real device draws it.
  // (A lead the recording simply does not have stays in the trace colour.)
  if (!missing) {
    ctx.strokeStyle = theme.off;
    ctx.lineWidth *= 1.6;
  }
  ctx.setLineDash([ppm, ppm]);
  ctx.stroke(dropout);
  ctx.restore();

  // Lead label
  ctx.font = `bold ${Math.round(3.2 * ppm)}px Arial`;
  ctx.textBaseline = 'top';
  ctx.textAlign = 'left';
  ctx.fillStyle = anyOff ? theme.off : theme.label;
  ctx.fillText(anyOff ? cell.label + ' · sin contacto' : cell.label,
               x0 + ppm, rowPos[cell.row].y0 + ppm);

  // Tick where one time segment ends and the next begins
  if (cell.boundary && view.frozen) {
    ctx.strokeStyle = theme.trace;
    ctx.beginPath();
    ctx.moveTo(x0, yBase - 3 * ppm);
    ctx.lineTo(x0, yBase + 3 * ppm);
    ctx.stroke();
  }
}

function draw(canvas) {
  const W = canvas.width;
  const H = canvas.height;
  if (W === 0 || H === 0) return;

  const ctx = canvas.getContext('2d');
  const ppm = W / PAGE_MM;                       // pixels per millimetre
  const { cells, rows } = buildCells();
  const top = TOP_MM * ppm;
  const rowPos = layoutRows(cells, rows, ppm, top, H - top - BOTTOM_MM * ppm);
  const theme = (view.frozen || view.style === 'paper') ? PAPER : SCREEN;
  const geo = { ppm, rowPos, H, theme };

  ctx.setTransform(1, 0, 0, 1, 0, 0);
  ctx.drawImage(gridLayer(W, H, ppm, theme), 0, 0);

  ctx.strokeStyle = theme.trace;
  ctx.lineJoin = 'round';
  ctx.lineWidth = Math.max(1.2, ppm * 0.22);

  for (const row of rowPos) drawCalibration(ctx, row.base, ppm);
  for (const cell of cells) drawCell(ctx, cell, geo);

  // Settings line, as printed along the bottom edge of a real ECG
  const hr = heartRate();
  ctx.fillStyle = theme.text;
  ctx.font = `${Math.round(3 * ppm)}px Arial`;
  ctx.textBaseline = 'alphabetic';
  const y = H - 2 * ppm;
  ctx.textAlign = 'left';
  ctx.fillText(
    `${view.speed} mm/s     ${gainLabel()} mm/mV     Filtro 0,5–50 Hz     FC ${hr === null ? '---' : hr} lpm`,
    GUTTER_MM * ppm, y);
  ctx.textAlign = 'right';
  const source = view.rec ? `Registro ${view.rec.patientId}  ·  ` : '';
  const mode = view.frozen ? 'ADQUIRIDO' : (view.held ? 'CONGELADO' : 'EN VIVO');
  ctx.fillText(source + mode, W - RIGHT_MM * ppm, y);
}

// ─── Device chrome: electrode map, ECG button, patient bar ─────────────────

/** Body diagram with one marker per electrode; loose ones get a red cross. */
function renderMap() {
  const marks = VEST_ELECTRODES.map(name => {
    const [x, y] = ELECTRODE_XY[name];
    const seated = electrodeSeated(name);
    const marker = seated
      ? `<circle cx="${x}" cy="${y}" r="6" fill="#00C853"/>`
      : `<circle cx="${x}" cy="${y}" r="7" fill="#C62828"/>` +
        `<path d="M${x - 3.5} ${y - 3.5}l7 7m0 -7l-7 7" stroke="#fff" stroke-width="1.8"/>`;
    const labelY = name.startsWith('C') ? y + 17 : y - 10;
    return marker + `<text x="${x}" y="${labelY}" text-anchor="middle" font-size="9" ` +
           `fill="${seated ? '#cfd6ff' : '#FF8888'}">${name}</text>`;
  }).join('');

  document.getElementById('ecg-map-figure').innerHTML =
    `<svg viewBox="0 0 200 240" role="img" aria-label="Mapa de electrodos">
       <circle cx="100" cy="26" r="17" fill="none" stroke="#6b7190" stroke-width="2"/>
       <path d="M82 46 Q100 54 118 46 L160 56 Q172 60 170 74 L164 150 L150 150 L150 100
                L146 236 L108 236 L100 170 L92 236 L54 236 L50 100 L50 150 L36 150
                L30 74 Q28 60 40 56 Z" fill="none" stroke="#6b7190" stroke-width="2"/>
       ${marks}
     </svg>`;

  const loose = VEST_ELECTRODES.filter(name => !electrodeSeated(name));
  let caption = 'Todos los electrodos tienen buen contacto.';
  if (!state.vestRequired) caption = 'Sin chaleco: electrodos simulados, todos conectados.';
  else if (!state.vestConnected) caption = 'Sin datos del chaleco — revisá el cable USB.';
  else if (loose.length) caption = 'Revisá: ' + loose.join(', ');
  document.getElementById('ecg-map-caption').textContent = caption;
}

/**
 * Push vest-dependent state to the DOM, but only when it changed — this runs
 * every frame and must not rebuild the map sixty times a second.
 */
function syncChrome() {
  const off = leadsOff();
  const hr = off.has('II') ? null : heartRate();
  const usable = view.flat || !!view.rec;
  const key = [state.vestRequired, state.vestConnected, state.vestMask,
               hr, usable, view.mapOpen].join('|');
  if (key !== view.uiKey) {
    view.uiKey = key;
    document.getElementById('ecg-hr').textContent = hr === null ? '--' : hr;
    document.getElementById('btn-ecg-acquire').classList.toggle('ready', usable && off.size === 0);
    document.getElementById('btn-ecg-map').classList.toggle('attention', off.size > 0);
    if (view.mapOpen) renderMap();
  }
  document.getElementById('ecg-clock').textContent =
    new Date().toLocaleTimeString('es-AR', { hour: '2-digit', minute: '2-digit', second: '2-digit' });
}

function frame(ts) {
  if (!view.running) return;
  view.animId = requestAnimationFrame(frame);

  const canvas = document.getElementById('ecg-paper');
  const resized = syncSize(canvas);
  syncChrome();

  if (view.frozen || view.held) {
    // A still page: only repaint when something changed.
    if (!view.dirty && !resized) return;
  } else {
    if (view.lastTs) view.t += (ts - view.lastTs) / 1000 * FS;
    view.lastTs = ts;
    logContact();
  }
  draw(canvas);
  view.dirty = false;
}

/** Paint immediately, outside the animation loop (before print / export). */
function drawNow() {
  const canvas = document.getElementById('ecg-paper');
  syncSize(canvas);
  draw(canvas);
  view.dirty = false;
}

// ─── Controls ──────────────────────────────────────────────────────────────

/** Load the recording for the selected rhythm and example. */
function loadRecord() {
  const def = RHYTHMS[view.rhythm] || {};
  view.flat = def.source === 'flat';
  const key = def.ecgKey || def.dataKey;
  view.rec = (!view.flat && key) ? getRecordingSet(key, view.example) : null;
  view.t = 0;
  view.lastTs = 0;
  view.offLog = [];
  view.dirty = true;
  setHeld(false);
  refreshControls();
}

/** Sync the dependent controls and the note under the toolbar. */
function refreshControls() {
  const single = isSingleLead();
  const usable = view.flat || !!view.rec;

  const exampleSel = document.getElementById('ecg-example');
  const count = view.rec ? view.rec.count : 1;
  exampleSel.innerHTML = '';
  for (let i = 0; i < count; i++) {
    const opt = document.createElement('option');
    opt.value = i;
    opt.textContent = `${i + 1} de ${count}`;
    exampleSel.appendChild(opt);
  }
  exampleSel.value = view.example;
  exampleSel.disabled = count < 2;

  document.getElementById('ecg-format').disabled = single || !usable;
  // The lead picker only means something for the rhythm strip or a single lead.
  document.getElementById('ecg-lead').disabled =
    single || !usable || !(view.format === '4x3' || view.format === '1');
  view.uiKey = '';

  const notes = [];
  if (!usable) {
    notes.push('Todavía no hay un registro real de este ritmo.');
  } else if (single) {
    notes.push('Este registro tiene una sola derivación real, por eso no se muestra en 12 derivaciones.');
  }
  if (view.rec && !view.flat) {
    const ref = leadData('II');
    if (ref && ref.mv === undefined) {
      notes.push('Amplitud no calibrada: el registro original no conserva los mV.');
    }
  }
  const note = document.getElementById('ecg-note');
  note.textContent = notes.join(' ');
  note.style.display = notes.length ? 'block' : 'none';
}

/**
 * Open or close the preview of the acquired page.
 *
 * The page always starts where the recording's loop starts. The recordings are
 * 10 s long and repeat, so any other start would put the splice between two
 * repetitions in the middle of the printed ECG. Taken live, it is the loop
 * that last finished; taken from Freeze, the loop being looked at.
 */
function setFrozen(frozen) {
  if (frozen) {
    const len = loopLength();
    const at = view.held ? view.pageStart : Math.floor(view.t) - len;
    view.winStart = Math.max(0, Math.floor(at / len) * len);
    setMapOpen(false);
  }
  view.frozen = frozen;
  view.lastTs = 0;
  view.dirty = true;
  document.getElementById('ecg-screen').classList.toggle('preview', frozen);
}

/** Samples in one Freeze page: the time window of one cell on the main screen. */
function pageLength() {
  const { cells } = buildCells();
  return Math.max(2, Math.round(cells[0].wMm / view.speed * FS));
}

/** Enter or leave Freeze. Entering shows the last complete window. */
function setHeld(held) {
  view.held = held;
  view.lastTs = 0;
  view.dirty = true;
  if (held) {
    const L = pageLength();
    view.pageStart = Math.max(0, (Math.floor(view.t / L) - 1) * L);
  }
  document.getElementById('ecg-screen').classList.toggle('held', held);
  document.getElementById('btn-ecg-freeze').textContent = held ? 'Continuar' : 'Congelar';
  if (held) refreshFreezeBar();
}

/** Move the frozen window one page back (-1) or forward (+1). */
function pageFreeze(direction) {
  const L = pageLength();
  const newest = Math.max(0, (Math.floor(view.t / L) - 1) * L);
  const oldest = Math.max(0, Math.ceil((view.t - HISTORY_SECONDS * FS) / L) * L);
  // Snapped first: a format or speed change can leave pageStart off the grid.
  const snapped = Math.floor(view.pageStart / L) * L;
  view.pageStart = Math.min(newest, Math.max(oldest, snapped + direction * L));
  view.dirty = true;
  refreshFreezeBar();
}

/** Say which stretch of the session the frozen window covers. */
function refreshFreezeBar() {
  const L = pageLength();
  const ago = s => Math.max(0, Math.round((view.t - s) / FS));
  document.getElementById('ecg-freeze-pos').textContent =
    `hace ${ago(view.pageStart)}–${ago(view.pageStart + L)} s`;
}

function setMapOpen(open) {
  view.mapOpen = open;
  document.getElementById('ecg-map').classList.toggle('open', open);
  if (open) renderMap();
}

/** Print the preview. The canvas is repainted first so it is never stale. */
function printPage() {
  drawNow();
  window.print();
}

function savePng() {
  drawNow();
  document.getElementById('ecg-paper').toBlob(blob => {
    if (!blob) return;
    const a = document.createElement('a');
    a.href = URL.createObjectURL(blob);
    a.download = 'ecg-' + view.rhythm.toLowerCase().replace(/[^a-z0-9]+/gi, '-') + '.png';
    a.click();
    URL.revokeObjectURL(a.href);
  }, 'image/png');
}

/**
 * Wire the electrocardiograph's own controls. Called once from ui.js.
 * Navigation in and out of the screen stays in ui.js with the other screens.
 */
export function initEcg() {
  // Same rhythms, same order as the monitor's selector, then anything that
  // only exists here (asystole — on the monitor that is the arrest button).
  const rhythmSel = document.getElementById('ecg-rhythm');
  const monitorOrder = [...document.getElementById('cfg-rhythm').options].map(o => o.value);
  const names = monitorOrder.concat(Object.keys(RHYTHMS).filter(n => !monitorOrder.includes(n)));
  for (const name of names) {
    const opt = document.createElement('option');
    opt.textContent = name;
    rhythmSel.appendChild(opt);
  }
  view.rhythm = rhythmSel.value;

  const leadSel = document.getElementById('ecg-lead');
  for (const lead of LEADS) {
    const opt = document.createElement('option');
    opt.textContent = lead;
    leadSel.appendChild(opt);
  }
  leadSel.value = view.lead;

  rhythmSel.addEventListener('change', () => {
    view.rhythm = rhythmSel.value;
    view.example = 0;
    loadRecord();
  });
  document.getElementById('ecg-example').addEventListener('change', e => {
    view.example = parseInt(e.target.value) || 0;
    loadRecord();
  });
  document.getElementById('ecg-format').addEventListener('change', e => {
    view.format = e.target.value;
    view.dirty = true;
    refreshControls();
    if (view.held) pageFreeze(0);
  });
  leadSel.addEventListener('change', () => { view.lead = leadSel.value; view.dirty = true; });
  document.getElementById('ecg-speed').addEventListener('change', e => {
    view.speed = parseInt(e.target.value);
    view.dirty = true;
    if (view.held) pageFreeze(0);
  });
  document.getElementById('ecg-gain').addEventListener('change', e => {
    const [limb, chest] = e.target.value.split('/').map(Number);
    view.gain = limb;
    view.chestGain = chest || limb;
    view.dirty = true;
  });

  document.getElementById('ecg-style').addEventListener('change', e => {
    view.style = e.target.value;
    view.dirty = true;
  });

  document.getElementById('btn-ecg-acquire').addEventListener('click', () => setFrozen(true));
  document.getElementById('btn-ecg-freeze').addEventListener('click', () => setHeld(!view.held));
  document.getElementById('btn-ecg-older').addEventListener('click', () => pageFreeze(-1));
  document.getElementById('btn-ecg-newer').addEventListener('click', () => pageFreeze(1));
  document.getElementById('btn-ecg-close').addEventListener('click', () => setFrozen(false));
  document.getElementById('btn-ecg-map').addEventListener('click', () => setMapOpen(!view.mapOpen));
  document.getElementById('btn-ecg-print').addEventListener('click', printPage);
  document.getElementById('btn-ecg-png').addEventListener('click', savePng);
  document.getElementById('btn-ecg-vest').addEventListener('click', toggleVest);
}

/** Start the electrocardiograph. The screen must already be visible. */
export async function startEcg() {
  try {
    await loadSignalData();
  } catch (err) {
    console.warn('Could not load signal data:', err.message);
  }
  setFrozen(false);
  loadRecord();
  view.running = true;
  view.animId = requestAnimationFrame(frame);
}

/** Stop the animation loop when leaving the screen. */
export function stopEcg() {
  view.running = false;
  if (view.animId) cancelAnimationFrame(view.animId);
}
