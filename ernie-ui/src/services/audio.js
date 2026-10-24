// Metronome recorder, ported from the Stack tab (static/index.html: record / recordWithClick / refTone) so a hum
// taken by voice reaches /midi/hum with the same bpm, bars and count_in_s contract.

let clickCtx;
const ctx = async () => {
  clickCtx ||= new AudioContext();
  await clickCtx.resume();
  return clickCtx;
};

const PC = { C: 0, D: 2, E: 4, F: 5, G: 7, A: 9, B: 11 };

export function keyRoot(text = "") {
  const m = text.trim().match(/^([A-Ga-g])([#b]?)/);
  if (!m) return null;
  const name = m[1].toUpperCase() + m[2];
  const pc = (PC[name[0]] + (m[2] === "#" ? 1 : m[2] === "b" ? -1 : 0) + 12) % 12;
  return { name, midi: 57 + ((pc - 9 + 12) % 12) }; // A3..G#4: easy humming range
}

// root + fifth, so the producer can tune to the key before the click starts
export async function refTone(midi, dur) {
  const c = await ctx();
  const t0 = c.currentTime + 0.05;
  for (const n of [midi, midi + 7]) {
    const o = c.createOscillator(), g = c.createGain();
    o.type = "sine";
    o.frequency.value = 440 * 2 ** ((n - 69) / 12);
    g.gain.setValueAtTime(0.0001, t0);
    g.gain.exponentialRampToValueAtTime(0.12, t0 + 0.08);
    g.gain.setValueAtTime(0.12, t0 + dur - 0.15);
    g.gain.exponentialRampToValueAtTime(0.0001, t0 + dur);
    o.connect(g).connect(c.destination);
    o.start(t0);
    o.stop(t0 + dur + 0.02);
  }
  await new Promise((r) => setTimeout(r, (dur + 0.1) * 1000));
}

function scheduleClicks(c, t0, beats, spb) {
  for (let b = 0; b < beats; b++) {
    const t = t0 + b * spb, o = c.createOscillator(), g = c.createGain();
    o.frequency.value = b % 4 ? 1000 : 1600; // accent each downbeat
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(0.5, t + 0.002);
    g.gain.exponentialRampToValueAtTime(0.0001, t + 0.04);
    o.connect(g).connect(c.destination);
    o.start(t);
    o.stop(t + 0.05);
  }
}

// Click starts with the recording and runs bars+1 bars (start in bar 1 or 2; the server finds the first bar with
// a note), then auto-stops. countIn = recording start -> first click as heard (latency only).
export async function recordWithClick(bpm, bars, onTick) {
  const c = await ctx();
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const rec = new MediaRecorder(stream, { mimeType: "audio/webm" });
  const chunks = [];
  rec.ondataavailable = (e) => chunks.push(e.data);

  const spb = 60 / bpm, lead = 0.2, total = bars + 1;
  let offset = lead, timer;
  const started = new Promise((resolve) => {
    rec.onstart = () => {
      const t0 = c.currentTime + lead;
      scheduleClicks(c, t0, 4 * total, spb);
      offset = lead + (c.outputLatency || 0);
      timer = setInterval(() => {
        const b = Math.floor((c.currentTime - t0) / spb);
        if (b >= 0 && b < 4 * total) onTick?.(Math.floor(b / 4) + 1, (b % 4) + 1, total);
      }, 40);
      resolve();
    };
  });
  rec.start();
  await started;
  await new Promise((r) => setTimeout(r, (offset + 4 * total * spb + spb / 2) * 1000));
  rec.stop();
  await new Promise((r) => (rec.onstop = r));
  clearInterval(timer);
  stream.getTracks().forEach((t) => t.stop());
  return { blob: new Blob(chunks, { type: "audio/webm" }), countIn: offset };
}
