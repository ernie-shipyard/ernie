import { useRef } from "react";
import { useConversationClientTool, useConversationControls, useConversationInput } from "@elevenlabs/react";

import { api } from "../services/api.js";
import { keyRoot, recordWithClick, refTone } from "../services/audio.js";

// Client tools the ElevenLabs agent calls. Names and parameters must match scripts/setup_agent.py.
// Each handler does the work against the local backend, posts a card to the chat, and returns a short
// string the agent turns into speech. Errors are returned, not thrown, so Ernie can say what went wrong.

export const CAPTURE_PREFIX = "[capture finished]";

const ONESHOT_LAYERS = new Set(["drums", "fx"]);
const SAMPLE_TAG = { drums: "drums", bass: "bass", pad: "pad", synth: "synth", fx: "fx", vocals: "vocal" };
const POLL_MS = 500;

const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
const say = (obj) => JSON.stringify(obj);

async function sessionTempo() {
  try {
    const t = (await api.session()).tempo;
    return t ? Math.round(t) : null;
  } catch {
    return null; // no Ableton and no mock: caller falls back
  }
}

export function useErnieTools({ addCard, updateCard, modeRef }) {
  const { sendUserMessage } = useConversationControls();
  const { setMuted } = useConversationInput();
  const listening = useRef(null); // {captureId, cardId}

  async function pollCapture(captureId, cardId, track) {
    for (;;) {
      if (listening.current?.captureId !== captureId) return; // cancelled or replaced
      let s;
      try {
        s = await api.listenStatus(captureId);
      } catch (e) {
        s = { status: "failed", phase: e.message, done: true };
      }
      updateCard(cardId, { status: s.status, phase: s.phase, result: s.done ? s : undefined });
      if (s.done) {
        listening.current = null;
        const { status, verdict, measurements, notes } = s;
        sendUserMessage(`${CAPTURE_PREFIX} ${say({ track, status, verdict, measurements, notes })}`);
        return;
      }
      await sleep(POLL_MS);
    }
  }

  useConversationClientTool("start_listening", async ({ track } = {}) => {
    if (listening.current) return "Already listening. Tell them to hit play, or call stop_listening first.";
    const label = track?.trim() || "master";
    try {
      const started = await api.listen(label);
      const cardId = addCard({ type: "listen", track: label, status: started.status, phase: started.phase });
      listening.current = { captureId: started.capture_id, cardId };
      pollCapture(started.capture_id, cardId, label);
      return `Armed on ${label} (plugin port ${started.plugin_port}). Waiting for playback. Results will arrive in a ${CAPTURE_PREFIX} message.`;
    } catch (e) {
      return `error: ${e.message}`;
    }
  });

  useConversationClientTool("stop_listening", async () => {
    const active = listening.current;
    if (!active) return "Nothing is being captured right now.";
    listening.current = null;
    try {
      await api.cancelListen(active.captureId);
    } catch {
      // already finished; the card shows the final state
    }
    updateCard(active.cardId, { status: "cancelled", phase: "Cancelled." });
    return "Capture cancelled.";
  });

  useConversationClientTool("generate_sound", async ({ layer = "synth", description = "", key = "", bpm = 0 } = {}) => {
    const oneshot = ONESHOT_LAYERS.has(layer);
    const tempo = bpm || (await sessionTempo()) || 120;
    const what = description.trim() || layer;
    const prompt = oneshot
      ? `${what}${key ? ` in ${key}` : ""}, ${layer === "fx" ? "clean, no music" : "tight one-shot, no reverb"}`
      : `4-bar ${what} loop${key ? ` in ${key}` : ""} at ${tempo} bpm, no drums`;
    const cardId = addCard({ type: "audio", title: `${layer}: ${what}`, prompt, pending: true });
    try {
      const out = oneshot ? await api.oneshot(prompt) : await api.loop(prompt);
      updateCard(cardId, { pending: false, url: out.url, cached: out.cached });
      return `Done, the ${oneshot ? "one-shot" : `8 second loop at ${tempo} bpm`} is in the chat to preview. Prompt used: ${prompt}`;
    } catch (e) {
      updateCard(cardId, { pending: false, error: e.message });
      return `error: ${e.message}`;
    }
  });

  useConversationClientTool("find_samples", async ({ layer = "drums", key = "", bpm = 0 } = {}) => {
    try {
      const tempo = bpm || (await sessionTempo());
      const out = await api.pickSamples({ tag: SAMPLE_TAG[layer] || layer, key, bpm: tempo });
      addCard({ type: "samples", layer, candidates: out.candidates });
      if (!out.candidates.length) return `No ${layer} samples in the library match. Offer to generate one.`;
      return say({
        found: out.candidates.length,
        top: out.candidates.slice(0, 3).map((c) => ({ name: c.name, key: c.key, bpm: c.bpm, tempo: c.tempo })),
      });
    } catch (e) {
      return `error: ${e.message}`;
    }
  });

  useConversationClientTool("record_hum", async ({ bars = 2, bpm = 0, layer = "", key = "" } = {}) => {
    const nBars = Math.min(8, Math.max(1, Math.round(bars) || 2));
    const tempo = bpm || (await sessionTempo()) || 120;
    const cardId = addCard({ type: "hum", layer, phase: "Get ready…" });
    // let Ernie finish "hum it after the clicks" so the click and the take are clean
    for (let i = 0; i < 40 && modeRef.current === "speaking"; i++) await sleep(250);
    await sleep(300);
    setMuted(true); // the agent shouldn't hear the hum as a reply
    try {
      const root = keyRoot(key);
      if (root) {
        updateCard(cardId, { phase: `Tuning… ${root.name}` });
        await refTone(root.midi, (2 * 60) / tempo);
      }
      const take = await recordWithClick(tempo, nBars, (bar, beat, total) =>
        updateCard(cardId, { phase: `● bar ${bar}/${total} · beat ${beat}` }),
      );
      updateCard(cardId, { phase: "Transcribing to MIDI…" });
      const out = await api.humToMidi({ ...take, bpm: tempo, bars: nBars, trackName: layer || "ernie melody" });
      updateCard(cardId, { phase: "", result: out });
      return say({ notes: out.info.note_count, tempo: out.info.tempo, first_notes: out.info.notes.slice(0, 8),
                   instrument: out.instrument, live_track_created: Boolean(out.track && !out.track.mock && !out.track.error) });
    } catch (e) {
      updateCard(cardId, { phase: "", error: e.message });
      return `error: ${e.message}`;
    } finally {
      setMuted(false);
    }
  });

  useConversationClientTool("get_session", async () => {
    try {
      return say(await api.session());
    } catch (e) {
      return `error: ${e.message}`;
    }
  });
}
