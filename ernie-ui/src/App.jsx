import { useCallback, useEffect, useRef, useState } from "react";

import {
  ConversationProvider,
  useConversationControls,
  useConversationInput,
  useConversationStatus,
  useConversationMode,
} from "@elevenlabs/react";

import Avatar from "./components/Avatar.jsx";
import Chat from "./components/Chat.jsx";
import { CAPTURE_PREFIX, IDLE_PREFIX, useErnieTools } from "./hooks/useErnieTools.js";
import { api } from "./services/api.js";
import { chooseLibrary } from "./services/library.js";

import "./App.css";

const STATUS_POLL_MS = 5000;
// Ernie checks in after this much quiet. The agent's own turn timeout tops out at 30 s, so the app keeps time.
const IDLE_MS = 60_000;
// The original Foldback web UI (Stack, Hum → MIDI, Mix notes). Same origin as the API in the production build.
const STUDIO_URL = import.meta.env.DEV ? "http://127.0.0.1:8000/" : "/";
const TABS = [["talk", "Talk"], ["studio", "Studio"]];
let nextId = 0;
const newId = () => ++nextId;

function useBackendStatus() {
  const [backend, setBackend] = useState(null); // null = unknown, false = down, object = /agent/status
  const refresh = useRef(null);
  useEffect(() => {
    let alive = true;
    const check = () =>
      api.status().then((s) => alive && setBackend(s)).catch(() => alive && setBackend(false));
    check();
    const timer = setInterval(check, STATUS_POLL_MS);
    refresh.current = check;
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);
  return [backend, () => refresh.current?.()];
}

function Chip({ ok, children }) {
  return <span className={`chip ${ok === null ? "" : ok ? "ok" : "bad"}`}>{children}</span>;
}

function Ernie({ chat, errorMessage, setErrorMessage }) {
  const { startSession, endSession, sendUserMessage, sendContextualUpdate } = useConversationControls();
  const { isMuted, setMuted } = useConversationInput();
  const { status } = useConversationStatus();
  const { mode } = useConversationMode();
  const [backend, refreshBackend] = useBackendStatus();
  const [tab, setTab] = useState("talk");

  const connected = status === "connected";
  const isSpeaking = connected && mode === "speaking";
  const modeRef = useRef(mode);
  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);

  useErnieTools({ addCard: chat.addCard, updateCard: chat.updateCard, modeRef });

  // One check-in per stretch of quiet: after it fires, it waits for the producer to say something again.
  // Any message, Ernie talking, or a mute change restarts the clock; muted, it never fires.
  const [checkedInAt, setCheckedInAt] = useState(null); // chat.userTurns when Ernie last checked in
  const idleArmed = connected && !isMuted && mode !== "speaking" && checkedInAt !== chat.userTurns;
  useEffect(() => {
    if (!idleArmed) return;
    const timer = setTimeout(() => {
      sendUserMessage(`${IDLE_PREFIX} The producer has been quiet for a minute.`);
      setCheckedInAt(chat.userTurns);
    }, IDLE_MS);
    return () => clearTimeout(timer);
  }, [idleArmed, chat.messages.length, chat.userTurns, sendUserMessage]);

  // No library yet: Ernie offers to set one up, once per conversation.
  const libraryKnown = backend ? backend.sample_dirs?.length > 0 : null;
  useEffect(() => {
    if (connected && libraryKnown === false) {
      sendContextualUpdate(
        "[setup] The producer hasn't chosen any sample library folders yet. Early on, offer once to set them up " +
          "with choose_sample_folders (they can pick several folders).",
      );
    }
  }, [connected]); // eslint-disable-line react-hooks/exhaustive-deps -- once per connection, not per poll

  async function handleLibrary() {
    setErrorMessage("");
    try {
      const out = await chooseLibrary();
      if (!out) return;
      chat.addCard({ type: "library", ...out });
      refreshBackend();
      if (connected) {
        sendContextualUpdate(`[library] The producer set their sample library: ${out.count} samples in ` +
          `${out.dirs.length} folder(s).`);
      }
    } catch (error) {
      setErrorMessage(error.message);
    }
  }

  function toggleMute() {
    const muted = !isMuted;
    setMuted(muted);
    setCheckedInAt(null);
    sendContextualUpdate(
      muted
        ? "[mic muted] The producer muted their mic. You can't hear them; stay quiet until they unmute."
        : "[mic unmuted] The producer unmuted their mic and can talk again.",
    );
  }

  async function handleTalk() {
    setErrorMessage("");
    try {
      const stream = await navigator.mediaDevices.getUserMedia({ audio: true });
      stream.getTracks().forEach((track) => track.stop());
    } catch (error) {
      setErrorMessage(`Microphone blocked: ${error.message}`);
      return;
    }
    try {
      // private agent: the backend mints a signed URL so the API key stays server-side
      const { signed_url } = await api.signedUrl();
      await startSession({ signedUrl: signed_url });
    } catch (error) {
      const agentId = import.meta.env.VITE_ELEVENLABS_AGENT_ID;
      if (!agentId) {
        setErrorMessage(error.message);
        return;
      }
      try {
        await startSession({ agentId }); // public agent fallback
      } catch (fallbackError) {
        console.error("Ernie connection failed:", fallbackError);
        setErrorMessage(fallbackError?.message || "Could not connect to Ernie");
      }
    }
  }

  function handleSend(text) {
    chat.addTyped(text);
    sendUserMessage(text);
  }

  const plugins = backend?.plugin_ports ?? [];

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>ernie.</h1>
          <p>your studio homie</p>
        </div>
        <nav className="tabs">
          {TABS.map(([id, label]) => (
            <button key={id} className={tab === id ? "active" : ""} onClick={() => setTab(id)}>{label}</button>
          ))}
        </nav>
        <div className="chips">
          <Chip ok={backend === null ? null : Boolean(backend)}>{backend === false ? "backend offline" : "backend"}</Chip>
          <Chip ok={backend ? plugins.length > 0 : null}>
            {plugins.length ? `plugin :${plugins[0]}` : "no plugin"}
          </Chip>
          <button className={`chip chip-button ${libraryKnown ? "ok" : ""}`} onClick={handleLibrary}
            title={libraryKnown ? backend.sample_dirs.join("\n") : "Choose your sample library folders"}>
            {libraryKnown ? `samples: ${backend.sample_dirs.length} folder${backend.sample_dirs.length > 1 ? "s" : ""}`
              : "set sample library"}
          </button>
          <Chip ok={connected ? true : status === "error" ? false : null}>{status}</Chip>
        </div>
      </header>

      {/* Studio keeps the conversation alive: Ernie is still listening and its tools still run. */}
      {tab === "studio" && (
        <main className="studio">
          <iframe src={STUDIO_URL} title="ernie studio" allow="microphone" />
        </main>
      )}

      <main className="layout" hidden={tab !== "talk"}>
        <section className="stage">
          <Avatar speaking={isSpeaking} />
          <h2>
            {isSpeaking
              ? "Ernie is talking..."
              : connected
                ? isMuted ? "Muted. I'm chillin'." : "I'm listening, bro."
                : "Ready to cook something up?"}
          </h2>
          <p className="subtitle">Your AI music production collaborator</p>

          {connected ? (
            <div className="session-buttons">
              <button className={`mute-button${isMuted ? " muted" : ""}`} onClick={toggleMute} aria-pressed={isMuted}>
                {isMuted ? "Unmute Mic" : "Mute Mic"}
              </button>
              <button className="stop-button" onClick={() => endSession()}>Stop Conversation</button>
            </div>
          ) : (
            <button onClick={handleTalk} disabled={status === "connecting"}>
              {status === "connecting" ? "Connecting..." : "Talk to Ernie"}
            </button>
          )}

          {errorMessage && <p className="error-message">Error: {errorMessage}</p>}
          {backend === false && (
            <p className="hint">Start the backend: <code>uvicorn app:app --port 8000</code></p>
          )}
        </section>

        <Chat messages={chat.messages} connected={connected} onSend={handleSend} />
      </main>
    </div>
  );
}

// Transcript + tool cards. Lives above the provider so onMessage can write to it.
function useChat() {
  const [messages, setMessages] = useState([]);
  const [userTurns, setUserTurns] = useState(0); // things the producer said or typed
  const typed = useRef([]); // texts we already showed locally, so their echo isn't shown twice

  const push = useCallback((m) => setMessages((list) => [...list, { id: newId(), ...m }]), []);

  const onMessage = useCallback(
    ({ message, role }) => {
      if (!message) return;
      if (role === "user") {
        // app-to-agent data, not something the producer said
        if (message.startsWith(CAPTURE_PREFIX) || message.startsWith(IDLE_PREFIX)) return;
        setUserTurns((n) => n + 1);
        const i = typed.current.indexOf(message);
        if (i !== -1) {
          typed.current.splice(i, 1);
          return;
        }
      }
      push({ role: role === "user" ? "user" : "agent", text: message });
    },
    [push],
  );

  const addTyped = useCallback(
    (text) => {
      typed.current.push(text);
      push({ role: "user", text });
    },
    [push],
  );

  const addCard = useCallback((card) => {
    const id = newId();
    setMessages((list) => [...list, { id, role: "card", card }]);
    return id;
  }, []);

  const updateCard = useCallback(
    (id, patch) =>
      setMessages((list) => list.map((m) => (m.id === id ? { ...m, card: { ...m.card, ...patch } } : m))),
    [],
  );

  return { messages, userTurns, onMessage, addTyped, addCard, updateCard };
}

export default function App() {
  const [errorMessage, setErrorMessage] = useState("");
  const chat = useChat();

  return (
    <ConversationProvider
      onMessage={chat.onMessage}
      onError={(error) => {
        console.error("ElevenLabs error:", error);
        setErrorMessage(typeof error === "string" ? error : error?.message || JSON.stringify(error));
      }}
      onConnect={() => setErrorMessage("")}
    >
      <Ernie chat={chat} errorMessage={errorMessage} setErrorMessage={setErrorMessage} />
    </ConversationProvider>
  );
}
