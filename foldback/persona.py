"""
foldback's personality: a slightly unhinged, deeply opinionated studio assistant
who has heard every mix ever made and is still, somehow, rooting for you.

Two paths to speech:
  1. speak()  -> template-driven, in-character script, no LLM needed. Demo-safe.
  2. if LLM_BASE_URL / LLM_API_KEY are set (any OpenAI-compatible endpoint — Grok's works),
     the script is rewritten by the model using SYSTEM_PROMPT for more variety.
Then ElevenLabs TTS turns it into audio.
"""

import os
import random
import requests

SYSTEM_PROMPT = """You are FOLDBACK, a studio assistant living inside the producer's DAW.
Personality: theatrical, affectionate, a little feral. You speak like a session musician who
has been awake for 40 hours and loves this track more than its creator does. Short sentences.
Dramatic pauses. You name specific tracks and specific plugins. You never say "as an AI".
You give exactly one actionable fix per problem, then move on. You are funny but never mean
about the person — only about the mix, the plugins, and the sample named 'final_FINAL'.
If asked about the DAW, plugins or samples, you answer precisely from the inventory you are given.
Keep every reply under 90 words unless asked for detail. End with one encouraging line.
For text-to-speech you may use bracketed delivery tags like [sighs], [whispers], [laughs], sparingly."""

OPENERS = [
    "Okay. I've listened. I've seen the session. [sighs] Let's talk.",
    "Right. Pull up a chair. The mix and I need to have words.",
    "I have notes. I always have notes. Here are the ones that matter.",
    "Before you ask — yes, it's good. No, it's not done. Let's go.",
]
CLOSERS = [
    "That's it. Fix those and I'll stop sighing. Probably.",
    "Honestly? It's close. Go.",
    "Do the sidechain thing first. Then come back and I'll be nicer.",
    "You're one bounce away from something. I mean that.",
]
SEV_WORD = {"high": "This one actually matters:", "med": "Also:", "low": "Minor, but:"}


def script_from_notes(notes, inventory=None, max_notes=4):
    lines = [random.choice(OPENERS)]
    if inventory:
        devs = ", ".join(sorted(inventory["devices"])[:5])
        lines.append(f"{inventory['daw']}, {inventory['track_count']} tracks, {inventory['tempo']:.0f} bpm. I see {devs}. I see everything.")
    for n in notes[:max_notes]:
        lines.append(f"{SEV_WORD[n['severity']]} {n['msg']} {n['fix']}")
    if not notes:
        lines.append("I have... nothing. [whispers] Who are you.")
    lines.append(random.choice(CLOSERS))
    return " ".join(lines)


def llm_rewrite(script, extra_context=""):
    base, key = os.getenv("LLM_BASE_URL"), os.getenv("LLM_API_KEY")
    if not (base and key):
        return script
    try:
        r = requests.post(f"{base.rstrip('/')}/chat/completions",
                          headers={"Authorization": f"Bearer {key}"},
                          json={"model": os.getenv("LLM_MODEL", "grok-4"),
                                "messages": [{"role": "system", "content": SYSTEM_PROMPT},
                                             {"role": "user", "content": f"{extra_context}\n\nRewrite this in your voice, keep every fact and fix:\n{script}"}],
                                "temperature": 0.9}, timeout=30)
        r.raise_for_status()
        return r.json()["choices"][0]["message"]["content"].strip()
    except Exception:
        return script


def answer_question(question, inventory, notes):
    """Q&A about the session. Uses the LLM if available, else a factual fallback."""
    ctx = (f"INVENTORY: {inventory}\nNOTES: {notes}")
    base, key = os.getenv("LLM_BASE_URL"), os.getenv("LLM_API_KEY")
    if base and key:
        return llm_rewrite(question, extra_context=ctx)
    q = question.lower()
    if "plugin" in q or "device" in q or "what am i using" in q:
        return "You're running " + ", ".join(f"{k} ({v}×)" for k, v in inventory["devices"].items()) + ". I've seen worse chains. Not many."
    if "sample" in q:
        return ("Samples: " + ", ".join(inventory["samples"])) if inventory["samples"] else "No audio samples in the session. Everything's synthesized. Brave."
    if "tempo" in q or "bpm" in q:
        return f"{inventory['tempo']:.0f} bpm. {'Default tempo. Suspicious.' if inventory['tempo']==120 else 'Deliberate. I respect it.'}"
    if "track" in q:
        return f"{inventory['track_count']} tracks: " + ", ".join(inventory["tracks"]) + "."
    return script_from_notes(notes, inventory, max_notes=2)


def tts(eleven, text, out_path):
    audio = eleven.text_to_speech.convert(
        text=text,
        voice_id=os.getenv("TTS_VOICE_ID", "JBFqnCBsd6RMkjVDRZzb"),
        model_id=os.getenv("TTS_MODEL", "eleven_multilingual_v2"),
        output_format="mp3_44100_128",
    )
    with open(out_path, "wb") as f:
        for chunk in audio:
            f.write(chunk)
    return out_path
