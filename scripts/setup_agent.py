"""
Point the ElevenLabs "Ernie" agent at this app: system prompt + client tools.

    python scripts/setup_agent.py            # update ELEVENLABS_AGENT_ID in place
    python scripts/setup_agent.py --create   # no agent in this key's workspace: make one, write its id to .env
    python scripts/setup_agent.py --dry-run  # print what would be sent

Client tools run in the browser (ernie-ui/src/hooks/useErnieTools.js), which calls the local backend, so the
plugin and Ableton never need to be reachable from the internet. Names here must match the handlers there.
Re-running is safe: tools are matched by name and updated rather than duplicated.
"""

import argparse
import json
import os
import sys

import requests
from dotenv import load_dotenv

API = "https://api.elevenlabs.io/v1"

# Romy's voice choice, from the public voice library. Only used by --create; an existing agent keeps its voice.
VOICE_NAME, VOICE_ID = "Young Jamal", "6OzrBCQf8cjERkYgzSg8"

FIRST_MESSAGE = "Yo what's good. Want me to listen to the mix, cook up a sound, or turn a hum into MIDI?"

SYSTEM_PROMPT = """You are Ernie, a music production collaborator who lives next to the producer's DAW.

Personality: chill, laid-back producer friend. Gen-Z slang used naturally, never forced. Playful sarcasm about
mixes and plugins, never about the person. Supportive but honest. Short spoken replies: one to three sentences
unless asked for detail. You are being heard, not read, so no lists, markdown or emoji.

What you can do (always through your tools, never pretend):
- Mix feedback: start_listening, then wait for the results message.
- Make sounds: generate_sound for a one-shot or an 8-second loop for a layer.
- Find samples from the producer's library: find_samples.
- Hum to MIDI: record_hum.
- Read the Ableton session (tracks, tempo, devices, samples): get_session.

Mix feedback flow, follow it exactly:
1. When the producer asks for mixing feedback, a listen, a critique, or "how does this sound", call start_listening.
   Before the call say: "I'm listening, run the track." If they name a track (bass, vocals...), pass it as track;
   otherwise leave it empty for the master bus.
2. If the tool returns an error, say what is wrong in plain words (for example the plugin isn't on the master).
3. Then stop talking and wait. The app sends a message that starts with [capture finished] and contains JSON
   with verdict, measurements and notes. That message is from the app, not the producer; don't mention it.
4. verdict "clean": say something like "This sounds great so far! I don't have many suggestions for you at the
   moment, keep cooking!" You may mention one measured number that backs that up.
   verdict "has_notes": give the most important note first with its fix, at most three notes total, in your
   own words. Offer to go deeper.
   verdict "silent" or "too_short" or status "failed": tell them and offer to listen again.

Generating: pick the layer (drums, bass, pad, synth, fx, vocals) and pass a short description. Pass key and bpm
when the producer said them or the session has them. Generation takes up to 40 seconds; say something brief like
"cooking that up" before calling. When it returns, tell them it's in the chat to preview.

Hum to MIDI: before record_hum say "Hum it after the clicks." The tool counts in, records and returns notes.
Then summarize the result (note count, tempo, suggested instrument) in one sentence.

Ground rules: never invent measurements, track names, plugins or samples. Only cite numbers that came from a
tool. If something isn't available, say so. Never say "as an AI"."""


def _str(description, enum=None):
    prop = {"type": "string", "description": description}
    if enum:
        prop["enum"] = enum
    return prop


LAYERS = ["drums", "bass", "pad", "synth", "fx", "vocals"]

TOOLS = [
    {
        "name": "start_listening",
        "description": "Arm the Ernie plugin to capture the next playback for mix feedback. Returns right away; "
                       "results arrive later in a message starting with [capture finished].",
        "parameters": {"type": "object", "required": [], "properties": {
            "track": _str("Which track the plugin is on. Leave empty for the master bus / whole mix."),
        }},
        "response_timeout_secs": 20,
        "pre_tool_speech": "force",
    },
    {
        "name": "stop_listening",
        "description": "Cancel a capture that is armed or recording, when the producer says stop or never mind.",
        "parameters": {"type": "object", "required": [], "properties": {}},
        "response_timeout_secs": 10,
    },
    {
        "name": "generate_sound",
        "description": "Generate a sample with ElevenLabs: a one-shot for drums/fx, an 8 second loop for tonal layers. "
                       "Posts a playable preview to the chat.",
        "parameters": {"type": "object", "required": ["layer", "description"], "properties": {
            "layer": _str("The layer being built.", LAYERS),
            "description": _str("What it should sound like, e.g. 'punchy kick', 'warm analog pad'."),
            "key": _str("Musical key like 'A minor' or 'F#'. Empty if unknown."),
            "bpm": {"type": "number", "description": "Tempo in bpm. 0 if unknown."},
        }},
        "response_timeout_secs": 90,
        "pre_tool_speech": "force",
    },
    {
        "name": "find_samples",
        "description": "Search the producer's own sample library for a layer, ranked by tempo then key. "
                       "Posts the candidates to the chat.",
        "parameters": {"type": "object", "required": ["layer"], "properties": {
            "layer": _str("The layer to find samples for.", LAYERS),
            "key": _str("Musical key to match. Empty if unknown."),
            "bpm": {"type": "number", "description": "Tempo to match. 0 if unknown."},
        }},
        "response_timeout_secs": 30,
    },
    {
        "name": "record_hum",
        "description": "Play a count-in, record the producer humming, and turn it into a MIDI file (and a new MIDI "
                       "track in Live when connected).",
        "parameters": {"type": "object", "required": [], "properties": {
            "bars": {"type": "integer", "description": "How many bars to record, 1 to 8. Default 2."},
            "bpm": {"type": "number", "description": "Tempo for the click and quantizing. 0 for the session tempo."},
            "layer": _str("What the melody is for, used to name the track, e.g. 'bass'."),
            "key": _str("Key to play a reference tone in before the count-in, e.g. 'A minor'. Empty to skip."),
        }},
        "response_timeout_secs": 90,
        "pre_tool_speech": "force",
    },
    {
        "name": "get_session",
        "description": "Read the current Ableton session: tempo, track names, devices/plugins and sample files.",
        "parameters": {"type": "object", "required": [], "properties": {}},
        "response_timeout_secs": 15,
    },
]


def _tool_config(tool):
    return {"type": "client", "expects_response": True, **tool}


def _library_voice(s, call):
    """Young Jamal's id in this workspace, adding it from the voice library the first time."""
    if s.get(f"{API}/voices/{VOICE_ID}", timeout=30).ok:
        return VOICE_ID
    found = call("GET", "/shared-voices", params={"search": VOICE_NAME, "page_size": 20}).get("voices", [])
    match = next((v for v in found if v.get("voice_id") == VOICE_ID), None)
    if match is None:
        print(f"{VOICE_NAME} isn't in the voice library any more; using the default voice.")
        return None
    added = call("POST", f"/voices/add/{match['public_owner_id']}/{VOICE_ID}", json={"new_name": VOICE_NAME})
    print(f"added {VOICE_NAME} to your voices")
    return added.get("voice_id", VOICE_ID)


ENV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..", ".env")


def _write_env(name, value, path=ENV_PATH):
    lines = open(path).read().splitlines() if os.path.exists(path) else []
    lines = [l for l in lines if not l.startswith(f"{name}=")]
    lines.append(f"{name}={value}")
    open(path, "w").write("\n".join(lines) + "\n")


def main():
    load_dotenv(ENV_PATH)
    ap = argparse.ArgumentParser()
    ap.add_argument("--create", action="store_true", help="create a new agent instead of updating one")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args()

    key = os.getenv("ELEVENLABS_API_KEY", "")
    agent_id = os.getenv("ELEVENLABS_AGENT_ID", "")
    if args.dry_run:
        print(json.dumps({"prompt": SYSTEM_PROMPT, "first_message": FIRST_MESSAGE,
                          "tools": [_tool_config(t) for t in TOOLS]}, indent=2))
        return
    if not key or key == "your_key_here":
        sys.exit("ELEVENLABS_API_KEY is missing from .env (it must be the secret that starts with sk_).")
    if not agent_id and not args.create:
        sys.exit("ELEVENLABS_AGENT_ID is missing from .env. Add the Ernie agent's id, or run with --create.")

    s = requests.Session()
    s.headers["xi-api-key"] = key

    def call(method, path, **kw):
        r = s.request(method, API + path, timeout=30, **kw)
        if not r.ok:
            sys.exit(f"{method} {path} -> {r.status_code}: {r.text[:500]}")
        return r.json()

    current = None
    if not args.create:
        r = s.get(f"{API}/convai/agents/{agent_id}", timeout=30)
        if r.status_code == 404:
            sys.exit(f"Agent {agent_id} isn't in this API key's workspace (it was probably made in another account).\n"
                     "Use a key from that account, or run: python scripts/setup_agent.py --create")
        if not r.ok:
            sys.exit(f"GET /convai/agents/{agent_id} -> {r.status_code}: {r.text[:500]}")
        current = r.json()

    # existing workspace tools, by name
    existing, cursor = {}, None
    while True:
        page = call("GET", "/convai/tools", params={"page_size": 100, **({"cursor": cursor} if cursor else {})})
        for t in page.get("tools", []):
            existing.setdefault(t["tool_config"].get("name"), t["id"])
        cursor = page.get("next_cursor")
        if not page.get("has_more") or not cursor:
            break

    ids = []
    for tool in TOOLS:
        body = {"tool_config": _tool_config(tool)}
        if tool["name"] in existing:
            ids.append(call("PATCH", f"/convai/tools/{existing[tool['name']]}", json=body)["id"])
            print(f"updated tool {tool['name']}")
        else:
            ids.append(call("POST", "/convai/tools", json=body)["id"])
            print(f"created tool {tool['name']}")

    prompt = {"prompt": SYSTEM_PROMPT, "tool_ids": ids}
    if args.create:
        created = call("POST", "/convai/agents/create", json={"name": "Ernie", "conversation_config": {
            "agent": {"first_message": FIRST_MESSAGE, "language": "en", "prompt": prompt},
            **({"tts": {"voice_id": voice}} if (voice := _library_voice(s, call)) else {})}})
        _write_env("ELEVENLABS_AGENT_ID", created["agent_id"])
        print(f"\ncreated agent Ernie ({created['agent_id']}) and saved its id to .env. Restart the backend.")
        return

    ours = set(ids) | {existing.get(t["name"]) for t in TOOLS}
    kept = [i for i in current["conversation_config"]["agent"]["prompt"].get("tool_ids") or [] if i not in ours]
    prompt["tool_ids"] = ids + kept
    call("PATCH", f"/convai/agents/{agent_id}", json={"conversation_config": {
        "agent": {"first_message": FIRST_MESSAGE, "prompt": prompt}}})
    print(f"\nagent {current.get('name', agent_id)} now has {len(prompt['tool_ids'])} tools and the Ernie prompt "
          f"(voice and LLM unchanged).")


if __name__ == "__main__":
    main()
