/**
 * canvas.js — Waveform rendering on HTML canvas elements.
 *
 * Pure rendering functions — no state imports, everything passed as parameters.
 * This means they're trivially testable and could render to an offscreen canvas.
 *
 * The rendering mimics a real bedside monitor's "sweep" style: the waveform
 * wraps around in a circular buffer, and a small gap appears at the write head
 * position, creating the characteristic "erasing" effect where new data
 * overwrites old data from left to right.
 */

/**
 * Sync the canvas resolution to its CSS display size.
 * Without this, the canvas renders at its default 300x150 resolution and gets
 * stretched/blurred. Called every frame to handle window resizes.
 *
 * @param {HTMLCanvasElement} canvas
 */
export function syncCanvasSize(canvas) {
  const w = canvas.clientWidth;
  const h = canvas.clientHeight;
  if (canvas.width !== w || canvas.height !== h) {
    canvas.width = w;
    canvas.height = h;
  }
}

/**
 * Draw a waveform from a circular buffer onto a canvas.
 *
 * The buffer is read starting from (writePos + 1) — the oldest sample — and
 * wraps around. A small gap near the write head creates the "sweep" effect
 * seen on real monitors: the gap separates old data (ahead of the sweep) from
 * new data (behind it).
 *
 * @param {HTMLCanvasElement} canvas - Target canvas element
 * @param {Float32Array} buffer - Circular buffer of signal samples
 * @param {number} writePos - Current write position in the buffer
 * @param {string} label - Waveform label (e.g. "ECG", "Pleth")
 * @param {string} color - CSS color for the waveform and label
 * @param {number} yMin - Minimum Y-axis value (signal units)
 * @param {number} yMax - Maximum Y-axis value (signal units)
 */
export function drawWaveform(canvas, buffer, writePos, label, color, yMin, yMax) {
  syncCanvasSize(canvas);
  const ctx = canvas.getContext('2d');
  const W = canvas.width;
  const H = canvas.height;
  if (W === 0 || H === 0) return;

  // Reset any prior transforms
  ctx.setTransform(1, 0, 0, 1, 0, 0);

  // Clear to black background
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, W, H);

  // Draw waveform label in top-left corner
  ctx.fillStyle = color;
  ctx.font = 'bold 15px Arial';
  ctx.fillText(label, 8, 22);

  const len = buffer.length;
  const yRange = yMax - yMin;
  // Gap size: ~0.8% of buffer length, minimum 6 samples.
  // This creates the visual separation at the sweep head.
  const gapSize = Math.max(6, Math.round(len * 0.008));

  ctx.strokeStyle = color;
  ctx.lineWidth = 1.8;
  ctx.lineJoin = 'round';
  ctx.beginPath();
  let started = false;

  for (let i = 0; i < len; i++) {
    // Read index: start from oldest sample (writePos + 1), wrap around
    const ri = (writePos + 1 + i) % len;

    // Skip samples near the write head to create the sweep gap
    const distToHead = (writePos - ri + len) % len;
    if (distToHead < gapSize) {
      if (started) ctx.stroke();
      ctx.beginPath();
      started = false;
      continue;
    }

    // Map sample index to X pixel and sample value to Y pixel
    const x = (i / len) * W;
    const y = H - ((buffer[ri] - yMin) / yRange) * H;

    if (!started) {
      ctx.moveTo(x, y);
      started = true;
    } else {
      ctx.lineTo(x, y);
    }
  }
  if (started) ctx.stroke();
}
