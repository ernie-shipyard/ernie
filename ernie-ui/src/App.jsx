
import { useState, useEffect } from "react";

import {
  ConversationProvider,
  useConversationControls,
  useConversationStatus,
  useConversationMode,
} from "@elevenlabs/react";

import "./App.css";

function Ernie({ errorMessage, setErrorMessage }) {
  const { startSession, endSession } = useConversationControls();
  const { status } = useConversationStatus();
  const { mode } = useConversationMode();

  const connected = status === "connected";
  const isSpeaking = connected && mode === "speaking";

  const [mouthOpen, setMouthOpen] = useState(false);
  const [tab, setTab] = useState("studio");

  // Animate Ernie's mouth while speaking
  useEffect(() => {
    if (!isSpeaking) {
      setMouthOpen(false);
      return;
    }

    const interval = setInterval(() => {
      setMouthOpen((previous) => !previous);
    }, 100);

    return () => clearInterval(interval);
  }, [isSpeaking]);

  async function handleTalk() {
    setErrorMessage("");

    const agentId = import.meta.env.VITE_ELEVENLABS_AGENT_ID;

    if (!agentId) {
      setErrorMessage("Missing Agent ID in .env.local");
      return;
    }

    try {
      const stream = await navigator.mediaDevices.getUserMedia({
        audio: true,
      });

      stream.getTracks().forEach((track) => track.stop());

      await startSession({ agentId });
    } catch (error) {
      console.error("Ernie connection failed:", error);
      setErrorMessage(
        error?.message || "Could not connect to Ernie"
      );
    }
  }

  return (
    <div className="app">
      <header className="header">
        <div>
          <h1>ernie.</h1>
          <p>your studio homie</p>
        </div>

        <span className="online">{status}</span>
      </header>

      <main className="content">
        {/* Ernie's animated character */}
        <div className="avatar">
          <img
            src={
              isSpeaking && mouthOpen
                ? "/images/ernie_talk.png"
                : "/images/ernie.png"
            }
            alt="Ernie"
            className="ernie-image"
          />
        </div>

        {connected ? (
          <button
            className="stop-button"
            onClick={() => endSession()}
          >
            Stop Conversation
          </button>
        ) : (
          <button
            onClick={handleTalk}
            disabled={status === "connecting"}
          >
            {status === "connecting"
              ? "Connecting..."
              : "Talk to Ernie"}
          </button>
        )}

        {errorMessage && (
          <p className="error-message">
            Error: {errorMessage}
          </p>
        )}

        {/* Talk: the conversation view. Studio: the Foldback web UI (Stack etc.) served by the backend */}
        <div style={{ display: "flex", gap: 10 }}>
          {[["talk", "Talk"], ["studio", "Studio"]].map(([id, label]) => (
            <button
              key={id}
              onClick={() => setTab(id)}
              style={{
                padding: "8px 22px",
                ...(tab === id
                  ? {}
                  : { background: "transparent", color: "var(--primary)", boxShadow: "inset 0 0 0 1px var(--primary)" }),
              }}
            >
              {label}
            </button>
          ))}
        </div>

        {tab === "talk" && (
          <>
            <h2>
              {isSpeaking
                ? "Ernie is talking..."
                : connected
                ? "I'm listening, bro."
                : "Ready to cook something up?"}
            </h2>

            <p className="subtitle">
              Your AI music production collaborator
            </p>

            <p className="hint">Status: {status}</p>
          </>
        )}

        {/* Always mounted, only hidden on Talk, so the Stack state and any playing audio survive tab switches */}
        <div style={{ width: "100%", marginTop: 20, display: tab === "studio" ? "block" : "none" }}>
          <iframe
            src="http://127.0.0.1:8000"
            title="ernie studio"
            style={{ width: "100%", height: "80vh", border: 0, borderRadius: 12 }}
            allow="microphone"
          />
        </div>
      </main>


    </div>
  );
}

export default function App() {
  const [errorMessage, setErrorMessage] = useState("");

  return (
    <ConversationProvider
      onError={(error) => {
        console.error("ElevenLabs error:", error);
        setErrorMessage(
          typeof error === "string"
            ? error
            : error?.message || JSON.stringify(error)
        );
      }}
      onConnect={() => setErrorMessage("")}
    >
      <Ernie
        errorMessage={errorMessage}
        setErrorMessage={setErrorMessage}
      />
    </ConversationProvider>
  );
}
