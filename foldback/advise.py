"""
Mixing & arrangement feedback.

Two sources:
  structural_notes(session)  -> rules over the session graph (free, instant, works on mock)
  sonic_notes(wav_path)      -> librosa over a bounce of the master (needs an export)

Each note is {"severity": "high|med|low", "track": name|None, "msg": str, "fix": str}
The persona turns these into speech; keep msgs factual here.
"""

import os
import re
import numpy as np
import librosa

DB0 = 0.85   # Live fader value for 0 dB


def _db(vol):   # rough: Live's fader is not linear-in-dB, but this is fine for advice
    return 20 * np.log10(max(vol, 1e-4) / DB0) * 1.0


def structural_notes(session):
    notes, tracks = [], session.tracks
    names = [t.name for t in tracks]

    # --- generic/unnamed tracks
    for t in tracks:
        if re.fullmatch(r"(Audio|MIDI|Track) ?\d+", t.name):
            notes.append(dict(severity="low", track=t.name, msg=f"Track '{t.name}' still has its default name.",
                              fix="Name it. Future-you will thank present-you."))

    # --- everything panned center
    if len(tracks) >= 4 and all(abs(t.pan) < 0.05 for t in tracks):
        notes.append(dict(severity="med", track=None, msg=f"All {len(tracks)} tracks are panned dead center.",
                          fix="Push hats, pads and doubles out to the sides; keep kick, bass and lead vocal in the middle."))

    # --- hot faders
    for t in tracks:
        if t.volume > DB0 + 0.03:
            notes.append(dict(severity="med", track=t.name, msg=f"'{t.name}' fader is above 0 dB (~+{_db(t.volume):.1f} dB).",
                              fix="Pull it down and bring everything else up to it, or you'll be fighting the limiter."))

    # --- kick + bass with no sidechain
    kick = next((t for t in tracks if re.search(r"kick|bd\b|drum", t.name, re.I)), None)
    bass = next((t for t in tracks if re.search(r"bass|sub|808", t.name, re.I)), None)
    if kick and bass and not any(re.search(r"compressor|glue|sidechain", d, re.I) for d in bass.devices):
        notes.append(dict(severity="high", track=bass.name, msg=f"'{bass.name}' has no compressor — nothing is ducking it under '{kick.name}'.",
                          fix="Drop a Compressor on the bass, sidechain it from the kick, fast attack, ~30 ms release."))

    # --- duplicate devices stacked
    for t in tracks:
        counts = {}
        for d in t.devices:
            counts[d] = counts.get(d, 0) + 1
        for d, c in counts.items():
            if c >= 2:
                notes.append(dict(severity="low", track=t.name, msg=f"'{t.name}' has {c}× {d} in series.",
                                  fix="Usually one does the job. Three compressors is a cry for help."))

    # --- reverb on individual tracks instead of a return
    reverby = [t.name for t in tracks if any(re.search(r"reverb|hall|plate", d, re.I) for d in t.devices)]
    if len(reverby) >= 2 and not session.returns:
        notes.append(dict(severity="med", track=None, msg=f"Reverb inserted directly on {len(reverby)} tracks and no return tracks.",
                          fix="Put one reverb on a return and send to it, so everything sits in the same room."))

    # --- empty tracks
    for t in tracks:
        if not t.clips and not t.devices:
            notes.append(dict(severity="low", track=t.name, msg=f"'{t.name}' is empty.", fix="Delete it or commit to it."))

    # --- sample hygiene
    for t in tracks:
        for c in t.clips:
            if re.search(r"final|v\d+|copy|untitled", c["name"], re.I) and c.get("file_path"):
                notes.append(dict(severity="low", track=t.name, msg=f"Sample '{c['name']}' has a filename that says 'final' at least once.",
                                  fix="It is never final. Consolidate and rename."))

    # --- no limiter / nothing on master
    if not session.master_devices:
        notes.append(dict(severity="low", track="Master", msg="Nothing on the master chain.",
                          fix="Fine for now — just don't export without at least a limiter."))

    # --- tempo sanity
    if session.tempo == 120.0:
        notes.append(dict(severity="low", track=None, msg="Tempo is exactly 120 — the default.",
                          fix="Did you choose that, or did Ableton?"))

    order = {"high": 0, "med": 1, "low": 2}
    return sorted(notes, key=lambda n: order[n["severity"]])


def inventory(session):
    """What the assistant 'recognizes': DAW, plugins/devices, samples."""
    devices, samples = {}, []
    for t in session.tracks:
        for d in t.devices:
            devices[d] = devices.get(d, 0) + 1
        for c in t.clips:
            if c.get("file_path"):
                samples.append(os.path.basename(c["file_path"]))
    return {"daw": session.daw, "tempo": session.tempo, "track_count": len(session.tracks),
            "tracks": [t.name for t in session.tracks], "devices": devices, "samples": samples}


# ----------------------------------------------------------------- sonic

def sonic_notes(wav_path):
    y2, sr = librosa.load(wav_path, sr=44100, mono=False)
    stereo = y2.ndim == 2
    y = y2.mean(axis=0) if stereo else y2
    notes = []
    corr = None
    if stereo:
        l, r = y2[0], y2[1]
        corr = float(np.corrcoef(l, r)[0, 1]) if l.std() > 0 and r.std() > 0 else 1.0
        if corr < 0.2:
            notes.append(dict(severity="high", track="Master", msg=f"Stereo correlation is {corr:.2f} — this will fall apart in mono.",
                              fix="Check the sides: wide pads and stereo delays are the usual culprits. Mono the sub."))
        elif corr > 0.95:
            notes.append(dict(severity="low", track="Master", msg="The mix is nearly mono.",
                              fix="Widen hats, pads or doubles; keep kick, bass and lead vocal centered."))
    S = np.abs(librosa.stft(y, n_fft=4096))
    freqs = librosa.fft_frequencies(sr=sr, n_fft=4096)
    spec = S.mean(axis=1)
    total = spec.sum() + 1e-9

    def band(lo, hi): return spec[(freqs >= lo) & (freqs < hi)].sum() / total

    sub, low, lowmid, mid, high = band(20, 60), band(60, 250), band(250, 500), band(500, 4000), band(4000, 20000)

    if lowmid > 0.22:
        notes.append(dict(severity="high", track="Master", msg=f"Low-mids (250–500 Hz) are {lowmid*100:.0f}% of the energy — that's mud.",
                          fix="Find the pad or guitar and cut 2–3 dB around 300 Hz."))
    if sub > 0.18:
        notes.append(dict(severity="med", track="Master", msg="A lot of energy under 60 Hz.",
                          fix="High-pass everything that isn't kick or bass at 80–100 Hz."))
    if high < 0.04:
        notes.append(dict(severity="med", track="Master", msg="Very little above 4 kHz — the mix sounds like it's behind a curtain.",
                          fix="Open up the hats, or a gentle high shelf on the master."))

    # dynamics / crest factor
    peak = np.max(np.abs(y)) + 1e-9
    rms = np.sqrt(np.mean(y ** 2)) + 1e-9
    crest = 20 * np.log10(peak / rms)
    if crest < 8:
        notes.append(dict(severity="high", track="Master", msg=f"Crest factor is {crest:.1f} dB — this is crushed.",
                          fix="Back the limiter off. Streaming services will turn it down anyway."))
    elif crest > 20:
        notes.append(dict(severity="low", track="Master", msg=f"Crest factor is {crest:.1f} dB — very dynamic.",
                          fix="Fine for a demo; a touch of glue compression if you want it to feel finished."))

    # stereo width can't be judged from mono; kick/bass clash via low-band modulation
    lowband = librosa.feature.rms(y=librosa.effects.preemphasis(y, coef=-0.97))[0]
    if np.std(lowband) / (np.mean(lowband) + 1e-9) < 0.25 and low > 0.3:
        notes.append(dict(severity="med", track="Master", msg="The low end is a constant wall — kick and bass aren't taking turns.",
                          fix="Sidechain the bass to the kick, or shorten the bass release."))

    lufs_approx = 20 * np.log10(rms) - 0.7      # un-K-weighted; close enough to flag a quiet or slammed bounce
    bands = dict(sub=sub, low=low, lowmid=lowmid, mid=mid, high=high, crest_db=crest,
                 peak_dbfs=20 * np.log10(peak), rms_dbfs=20 * np.log10(rms), lufs_approx=lufs_approx,
                 stereo_correlation=corr if corr is not None else 1.0)
    return notes, {k: round(float(v), 3) for k, v in bands.items()}
