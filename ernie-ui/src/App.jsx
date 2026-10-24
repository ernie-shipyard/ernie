import { useCallback, useEffect, useRef, useState } from "react";

import {
  ConversationProvider,
  useConversationControls,
  useConversationStatus,
  useConversationMode,
} from "@elevenlabs/react";

import Avatar from "./components/Avatar.jsx";
import Chat from "./components/Chat.jsx";
import { CAPTURE_PREFIX, useErnieTools } from "./hooks/useErnieTools.js";
import { api } from "./services/api.js";

import "./App.css";

const STATUS_POLL_MS = 5000;
// The original Foldback web UI (Stack, Hum → MIDI, Mix notes). Same origin as the API in the production build.
const STUDIO_URL = import.meta.env.DEV ? "http://127.0.0.1:8000/" : "/";
const TABS = [["talk", "Talk"], ["studio", "Studio"]];
let nextId = 0;
const newId = () => ++nextId;

function useBackendStatus() {
  const [backend, setBackend] = useState(null); // null = unknown, false = down, object = /agent/status
  useEffect(() => {
    let alive = true;
    const check = () =>
      api.status().then((s) => alive && setBackend(s)).catch(() => alive && setBackend(false));
    check();
    const timer = setInterval(check, STATUS_POLL_MS);
    return () => {
      alive = false;
      clearInterval(timer);
    };
  }, []);
  return backend;
}

function Chip({ ok, children }) {
  return <span className={`chip ${ok === null ? "" : ok ? "ok" : "bad"}`}>{children}</span>;
}

function Ernie({ chat, errorMessage, setErrorMessage }) {
  const { startSession, endSession, sendUserMessage } = useConversationControls();
  const { status } = useConversationStatus();
  const { mode } = useConversationMode();
  const backend = useBackendStatus();
  const [tab, setTab] = useState("talk");

  const connected = status === "connected";
  const isSpeaking = connected && mode === "speaking";
  const modeRef = useRef(mode);
  useEffect(() => {
    modeRef.current = mode;
  }, [mode]);

  useErnieTools({ addCard: chat.addCard, updateCard: chat.updateCard, modeRef });

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
            {isSpeaking ? "Ernie is talking..." : connected ? "I'm listening, bro." : "Ready to cook something up?"}
          </h2>
          <p className="subtitle">Your AI music production collaborator</p>

          {connected ? (
            <button className="stop-button" onClick={() => endSession()}>Stop Conversation</button>
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
  const typed = useRef([]); // texts we already showed locally, so their echo isn't shown twice

  const push = useCallback((m) => setMessages((list) => [...list, { id: newId(), ...m }]), []);

  const onMessage = useCallback(
    ({ message, role }) => {
      if (!message) return;
      if (role === "user") {
        if (message.startsWith(CAPTURE_PREFIX)) return; // app-to-agent data, not something the producer said
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

  return { messages, onMessage, addTyped, addCard, updateCard };
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
