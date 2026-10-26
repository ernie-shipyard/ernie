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

export const COUNT_IN = 4; // beats of count-in before bar 1

// count-in clicks are higher and louder than the metronome, so "1 2 3 4" can't be mistaken for bar 1
function scheduleClicks(c, t0, beats, spb) {
  for (let b = 0; b < beats; b++) {
    const t = t0 + b * spb, o = c.createOscillator(), g = c.createGain(), counting = b < COUNT_IN;
    o.frequency.value = counting ? 2400 : (b - COUNT_IN) % 4 ? 1000 : 1600; // accent each downbeat
    g.gain.setValueAtTime(0.0001, t);
    g.gain.exponentialRampToValueAtTime(counting ? 0.8 : 0.5, t + 0.002);
    g.gain.exponentialRampToValueAtTime(0.0001, t + (counting ? 0.07 : 0.04));
    o.connect(g).connect(c.destination);
    o.start(t);
    o.stop(t + 0.05);
  }
}

// Click starts with the recording: a 4-beat count-in, then `bars` bars of metronome, then it auto-stops.
// countIn = recording start -> bar 1's first click as heard (latency + count-in), which is beat 0 on the server.
// onTick(bar, beat, total) gets bar 0 during the count-in.
export async function recordWithClick(bpm, bars, onTick) {
  const c = await ctx();
  const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
  const rec = new MediaRecorder(stream, { mimeType: "audio/webm" });
  const chunks = [];
  rec.ondataavailable = (e) => chunks.push(e.data);

  const spb = 60 / bpm, lead = 0.2, beats = COUNT_IN + 4 * bars;
  let offset = lead, timer;
  const started = new Promise((resolve) => {
    rec.onstart = () => {
      const t0 = c.currentTime + lead;
      scheduleClicks(c, t0, beats, spb);
      offset = lead + (c.outputLatency || 0);
      timer = setInterval(() => {
        const b = Math.floor((c.currentTime - t0) / spb);
        if (b >= 0 && b < COUNT_IN) onTick?.(0, b + 1, bars);
        else if (b >= COUNT_IN && b < beats) onTick?.(Math.floor((b - COUNT_IN) / 4) + 1, ((b - COUNT_IN) % 4) + 1, bars);
      }, 40);
      resolve();
    };
  });
  rec.start();
  await started;
  await new Promise((r) => setTimeout(r, (offset + beats * spb + spb / 2) * 1000));
  rec.stop();
  await new Promise((r) => (rec.onstop = r));
  clearInterval(timer);
  stream.getTracks().forEach((t) => t.stop());
  return { blob: new Blob(chunks, { type: "audio/webm" }), countIn: offset + COUNT_IN * spb };
}
