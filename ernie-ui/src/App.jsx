
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

        <p className="hint">Status: {status}</p>

        {errorMessage && (
          <p className="error-message">
            Error: {errorMessage}
          </p>
        )}
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
