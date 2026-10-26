import { useEffect, useRef, useState } from "react";

import { api } from "../services/api.js";

const fmt = (v, unit = "", digits = 1) => (v === null || v === undefined ? "–" : `${v.toFixed(digits)}${unit}`);

function ListenCard({ card }) {
  const r = card.result;
  const live = !r && card.status !== "cancelled";
  return (
    <div className="card">
      <div className="card-title">
        {live && <span className="pulse" />}
        Listening to {card.track === "master" ? "the mix" : card.track}
      </div>
      <div className="card-sub">{card.phase}</div>
      {r?.measurements && (
        <>
          <div className="meters">
            <span>peak <b>{fmt(r.measurements.peak_dbfs, " dBFS")}</b></span>
            <span>RMS <b>{fmt(r.measurements.rms_dbfs, " dBFS")}</b></span>
            <span>crest <b>{fmt(r.measurements.crest_factor_db, " dB")}</b></span>
            <span>corr <b>{fmt(r.measurements.stereo_correlation, "", 2)}</b></span>
            <span>length <b>{fmt(r.measurements.duration_seconds, " s")}</b></span>
          </div>
          {r.notes?.length ? (
            <ul className="notes">
              {r.notes.map((n, i) => (
                <li key={i} className={`note ${n.priority}`}>
                  {n.message} <span className="fix">{n.fix}</span>
                </li>
              ))}
            </ul>
          ) : (
            r.verdict === "clean" && <div className="clean">No notes. Keep cooking.</div>
          )}
        </>
      )}
    </div>
  );
}

function AudioCard({ card }) {
  return (
    <div className="card">
      <div className="card-title">{card.title}</div>
      <div className="card-sub">{card.prompt}</div>
      {card.pending && <div className="card-sub"><span className="pulse" /> cooking… (up to ~40 s)</div>}
      {card.error && <div className="error-message">{card.error}</div>}
      {card.url && <audio controls src={card.url} />}
    </div>
  );
}

function SamplesCard({ card }) {
  return (
    <div className="card">
      <div className="card-title">{card.layer} from your library</div>
      {!card.candidates.length && <div className="card-sub">No matches. Ask Ernie to generate one.</div>}
      {card.candidates.map((c) => (
        <div key={c.path} className="sample">
          <div>
            {c.name}
            <span className="card-sub"> {[c.key, c.bpm && `${c.bpm} bpm`, c.tempo].filter(Boolean).join(" · ")}</span>
          </div>
          <audio controls preload="none" src={api.sampleUrl(c.path)} />
        </div>
      ))}
    </div>
  );
}

function HumCard({ card }) {
  const r = card.result;
  return (
    <div className="card">
      <div className="card-title">
        {!r && !card.error && <span className="pulse" />}Hum → MIDI{card.layer ? `: ${card.layer}` : ""}
      </div>
      {card.count && <div className="count-in">{card.count}</div>}
      {card.phase && <div className="card-sub">{card.phase}</div>}
      {card.error && <div className="error-message">{card.error}</div>}
      {r && (
        <>
          <div className="strip">{r.info.notes.map((n, i) => <span key={i}>{n}</span>)}</div>
          <div className="card-sub">
            <a href={r.midi} download>download .mid</a> · {r.info.note_count} notes at {r.info.tempo} bpm
            {r.instrument && ` · try ${r.instrument}`}
            {r.track && !r.track.mock && !r.track.error &&
              (r.track.clip_slot != null
                ? " · clip in the new track's first slot in Session view, copy and paste it wherever you want"
                : " · new MIDI track in Live")}
          </div>
        </>
      )}
    </div>
  );
}

function LibraryCard({ card }) {
  const tags = Object.entries(card.tags || {}).filter(([, n]) => n);
  return (
    <div className="card">
      <div className="card-title">Sample library: {card.count} samples</div>
      <div className="card-sub">{card.dirs.join(" · ")}</div>
      {tags.length > 0 && <div className="strip">{tags.map(([t, n]) => <span key={t}>{n} {t}</span>)}</div>}
    </div>
  );
}

const CARDS = { listen: ListenCard, audio: AudioCard, samples: SamplesCard, hum: HumCard, library: LibraryCard };

export default function Chat({ messages, connected, onSend }) {
  const [draft, setDraft] = useState("");
  const end = useRef(null);

  useEffect(() => {
    end.current?.scrollIntoView({ behavior: "smooth", block: "end" });
  }, [messages]);

  function submit(e) {
    e.preventDefault();
    const text = draft.trim();
    if (!text) return;
    onSend(text);
    setDraft("");
  }

  return (
    <section className="chat">
      <div className="log">
        {!messages.length && (
          <p className="empty">
            Try “give me some mixing feedback”, “make me a warm pad in A minor”, or “let me hum a bass line”.
          </p>
        )}
        {messages.map((m) => {
          if (m.role === "card") {
            const Card = CARDS[m.card.type];
            return <div key={m.id} className="row agent">{Card && <Card card={m.card} />}</div>;
          }
          return (
            <div key={m.id} className={`row ${m.role}`}>
              <div className="bubble">{m.text}</div>
            </div>
          );
        })}
        <div ref={end} />
      </div>
      <form className="composer" onSubmit={submit}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={connected ? "Or type to Ernie…" : "Start a conversation to chat"}
          disabled={!connected}
        />
        <button type="submit" disabled={!connected || !draft.trim()}>Send</button>
      </form>
    </section>
  );
}
