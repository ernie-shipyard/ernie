# ernie — handoff: audio→MIDI, sample discovery, Stack tab

Owner: Kristyn (`kristyn/midi-samples`). Written 2026-10-24 ~14:30 for teammates and their AI assistants merging this lane into `main`.
Everything below describes the state of the branch at that time; the last few commits (tempo matching, key-from-sample, tune-then-hum, stack playback) were built by Claude Code from prompts and verified by it with synthetic audio and real ElevenLabs calls, not yet by a human in a browser unless noted.

---

## 1. What this lane is

Three user-facing things, all in the companion app (`app.py` + `static/index.html`), none depending on the JUCE plugin or `mixengine/`:

1. **Stack tab** (default tab). Build a track one layer at a time — Drums, Bass, Pad, Synth/Lead, FX, Vocals. Each row: pick a sample from the producer's own library (key- and tempo-matched), generate one with ElevenLabs (Sound Effects for one-shots, Music API for loops), regenerate by voice, or hum a melody that becomes MIDI on that row. "Play stack" plays all chosen layers together.
2. **Hum → MIDI tab.** Hum → `.mid` + instrument suggestion. Beatbox → `.mid` on the GM drum map + closest library samples per hit. Beatbox is **greyed out "in progress"** in the UI because classification isn't demo-reliable yet (see §6).
3. **Hum → Track tab** (original feature). Hum → full instrumental via Music API, revise by voice.

Pitch to judges: ernie reads what you have, builds the next layer with you, and critiques the result. ElevenLabs is used as Scribe (hear), TTS (speak), Music + Sound Effects (build).

---

## 2. Files owned by this lane

| File | What |
|---|---|
| `foldback/hum2midi.py` | hum → notes → quantize → `.mid`; beatbox → onsets → classify → `.mid`; metronome/grid alignment; prototype classifier + calibration |
| `foldback/samples.py` | library index (`SAMPLE_DIRS`), filename tag/key/bpm parsing, feature fingerprint, nearest-sample match, instrument suggestion |
| `foldback/prototypes.json` | seed kick/snare/hat prototypes `{mean, std, hits}` per class. Seed data from one take; replace via calibration |
| `static/index.html` | Stack tab, Hum → MIDI tab, Hum → Track tab, metronome recorder, calibration UI, stack playback. Shared file — other tabs (Session, Mix notes, Talk to it) are other lanes |
| `app.py` | routes listed in §3. Shared file — only these routes belong to this lane |

Not mine, don't change on my behalf: `foldback/advise.py`, `foldback/persona.py`, `foldback/live.py`, `mixengine/`, `source/`, `tests/`.

---

## 3. Routes (all in `app.py`)

Stack:
- `GET /samples/pick?tag=&key=&bpm=` → up to 5 candidates. Sort: tempo group (exact → ±3% → unknown → far) then key (exact → relative → none → clash). Returns `name, path, tag, key, bpm, tempo`.
- `GET /sample/file?path=` → serves a file **only if it's in the index** (prevents arbitrary reads).
- `POST /generate/oneshot` → ElevenLabs Sound Effects (`text_to_sound_effects.convert`), 0.5–2 s, prompt built from row + key. Returns `/track/{id}`.
- `POST /compose` (existing) now takes optional `length_ms` (default 15000; Stack uses 8000). Prompt example: `"4-bar bass loop in A minor at 124 bpm, no drums"`.
- `POST /revise` (existing) — Scribe transcribes the instruction, edits the row's prompt, caller re-generates.
- `POST /midi/hum` — now takes optional `bpm, bars, count_in_s, track_name`. With `bpm`: no tempo estimation, quantize to 16ths at that tempo, bar 1 = first bar containing a note, keep `bars` bars. Returns `midi` link, `info` (tempo, note_count, notes), `track`, `instrument`, `key_center` (root only; **not applied to the UI** by design).

Hum → MIDI / beatbox:
- `POST /midi/beatbox` — optional `bpm, bars, count_in_s`. With `bpm`: grid-aware detection (per-16th-slot strongest onset, peak gate 25%, bar-1-from-first-hit). Without: estimated tempo, 200 ms debounce. Returns `.mid` link + `samples` (closest library files per hit type) + `say`.
- `POST /calibrate/{kick|snare|hat}` — one 2-bar click-to-start clip per class; responds with hits, centroid, peak; rejects clips that are too quiet or have <2 hits; warns when two classes are closer than 1.5× their combined spread. In-memory until saved.
- `POST /calibrate/save` → writes `foldback/prototypes.json`.
- `POST /samples/scan` → index `SAMPLE_DIRS` (cached in `foldback/.sample_index.json`, gitignored). `POST /samples/match` → one sound in, nearest samples out.
- `GET /midi/{id}`, `GET /track/{id}` — file serving.

Logging: one line per `/calibrate` and `/midi/beatbox` on uvicorn's logger: `bpm bars count_in_s upload_bytes decoded_s hits_kept`.

---

## 4. Decisions and why (so nobody reverts them by accident)

- **Key comes from the chosen sample, not the hum.** Library anchors key; the human tunes to it. Before a hum, the UI plays root+fifth for 2 beats ("tuning… A"), then the count-in. `key_center` from the hum is returned but ignored.
- **Tempo is never estimated when the metronome is on.** Beat-tracking a lone voice is unreliable; the session tempo is passed through and quantization is exact. Estimation remains the fallback when metronome is off.
- **No discard-the-count-in.** Users play on the first click. The click runs from the start; bar 1 is the first bar containing a hit/note above the gate. Count-in latency (`count_in_s`) is an offset only.
- **Click-to-start, auto-stop everywhere in this lane.** Hold-to-record was the cause of 2.3 s-short calibration clips (mouse drift → `mouseleave`). "Hold to ask/revise" on other tabs are the voice lane's and fine for 2 s questions.
- **Classifier = nearest prototype, std-scaled, not thresholds.** Thresholds tuned on real drums fail on mouths (laptop mics pass nothing <180 Hz; a beatboxed kick has a ~350–450 Hz centroid). Prototypes are "the average of what this person's mouth makes." Calibration overwrites them.
- **Beatbox greyed out in the UI.** Rhythm (onset → slot) is reliable; kick/snare/hat labels are not yet. Rather than demo an 80%-right classifier, the feature is marked in progress. Code path is intact and callable.
- **Tempo matching is filename-based** (`_130_`, `120bpm`, `75_bpm`, `124_`; ignores 808/_12_/_01). Key matching is filename-based (`Cmin`, `F#maj`, `Amin`, `Ebmajor`, `D_minor`, `Gm7`, bare trailing root). No audio-based key/tempo detection. Generated loops are assumed at the prompted tempo (Music API is not guaranteed exact).
- **Loops are pickable in a row only if the filename names that layer** (drum/beat/perc → Drums, bass → Bass, pad/synth/keys → Pad/Synth), so untagged loops don't flood rows.
- **Stack playback**: all rows start at one `AudioContext` time, 2-bar loop at session tempo; one-shots on beat 1 of each bar; `playbackRate` stretch applied to the **Drums row only** when the loop's bpm differs; tonal rows are never stretched (pitch would shift) — mismatch is flagged instead. No server changes.
- **`.env` loaded in `foldback/live.py` at import**, because `app.py` imports it before its own `load_dotenv()`; without this `FOLDBACK_MOCK` is ignored.

---

## 5. Environment

```
ELEVENLABS_API_KEY=sk_...        # must be the secret (starts sk_), NOT the key ID shown in the list
FOLDBACK_MOCK=1                   # read foldback/session.mock.json instead of Ableton
SAMPLE_DIRS=/Users/<you>/Splice/sounds[:more:paths]
TRACK_LENGTH_MS=15000 MUSIC_MODEL=music_v2_5 TTS_VOICE_ID=... TTS_MODEL=eleven_multilingual_v2
```
Needs `ffmpeg` on PATH (browser audio arrives as webm). After merging `main`, `pip install -r requirements.txt` again (`mixengine/` added deps) and restart uvicorn.

Verified today with a real key: TTS ✔, Sound Effects one-shot ✔, Music 8 s loop ✔. Scribe not yet exercised by a human.

---

## 6. Known gaps (be honest in the demo)

- Beatbox classification: on a real take, kicks ~7/8 correct; snare vs kick margins thin; calibration flow works mechanically but no human has completed a full calibrate → score cycle. Next step if resumed: MFCC features (13 coeffs) + kNN over all calibration hits instead of 7 hand features + mean prototype. Pretrained options (YAMNet, CLAP zero-shot) are heavier.
- Long "tsss" can register as two hits (rise-check not implemented).
- Melody: glides are snapped to intermediate semitones; min note length 80 ms.
- Key is root-only (A, not A minor); relative-key matching partly compensates.
- Library with no vocal/FX files → those rows say "No matches" until generated.
- Stack playback has no transport sync with Live; layers preview in the browser only.
- `/samples/pick` without `bpm` reports `tempo: "unknown"` for every candidate (UI always sends it).

---

## 7. Merging into `main` — what to expect

- `app.py`: `main` added CORS middleware, `from mixengine.main import app as mix_analysis_app, registry as mix_registry`, and a richer `/health`. This lane added route blocks (`/samples/pick`, `/sample/file`, `/generate/oneshot`, `/calibrate/*`, params on `/midi/*`, `length_ms` on `/compose`). A trial merge auto-merged with no conflicts; if a conflict appears it will be in the imports or `/health` — keep **both** sides.
- `README.md`: `main` rewrote it. Add this lane as a section under "Other features" (text in §8); don't restore the old README.
- `docs/ARCHITECTURE.md`: `main` updated the plugin/mixengine parts. Keep theirs; keep the "Audio → MIDI → samples" section and the ownership row for this lane.
- `static/index.html`: only this lane changed it since the branch point. Should merge clean.
- `.gitignore`: `main` removed `.DS_Store` tracking; this lane ignores `foldback/.sample_index.json`. Keep both. `ernie-*.json` capture outputs at repo root should also be ignored (not done).

---

## 8. README section (paste under "Other features")

**Stack tab.** Builds a track one layer at a time: Drums, Bass, Pad, Synth/Lead, FX, Vocals. Header shows session tempo and a key, set from the chosen sample's filename. *Pick from library* (`GET /samples/pick?tag=&key=&bpm=`) returns up to 5 indexed samples ranked by tempo then key, with mismatches flagged. *Preview* plays the chosen file. *Generate* calls ElevenLabs Sound Effects for Drums/FX one-shots (`POST /generate/oneshot`) or the Music API for 8 s loops on tonal rows (`POST /compose`, prompt includes key and tempo). *Regenerate by voice* sends your instruction through Scribe (`POST /revise`) and regenerates. *Hum melody* plays the key as a reference tone, runs a metronome, and turns the hum into quantized MIDI on a new Live track named after the row (`POST /midi/hum`). *Play stack* loops all chosen layers together in the browser at the session tempo. Beatbox → drums is in progress.

---

## 9. Demo script (60–90 s)

1. Stack tab → Drums → Pick → chip shows "122 bpm" → Preview.
2. Bass → Pick → key box fills "A min · from the Bass sample".
3. Bass → Hum melody → hear tone, click, hum 2 bars → notes strip + `.mid` + "new track in Live: Bass".
4. Pad → Generate (pre-generated before walking up) → Regenerate by voice: "warmer".
5. Play stack.
6. Mix notes → Advise my session → it talks.

Pre-generate Drums and Bass before the table; Music API takes 20–40 s per loop.
