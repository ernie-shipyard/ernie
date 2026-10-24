import { useEffect, useState } from "react";

const BASE = import.meta.env.BASE_URL;
export const MOUTH_INTERVAL_MS = 100;

// Ernie's animated character: mouth flaps while the agent is speaking, closed otherwise.
export default function Avatar({ speaking, interval = MOUTH_INTERVAL_MS }) {
  const [mouthOpen, setMouthOpen] = useState(false);

  useEffect(() => {
    if (!speaking) return;
    const timer = setInterval(() => setMouthOpen((previous) => !previous), interval);
    return () => {
      clearInterval(timer);
      setMouthOpen(false);
    };
  }, [speaking, interval]);

  return (
    <div className="avatar">
      <img
        src={speaking && mouthOpen ? `${BASE}images/ernie_talk.png` : `${BASE}images/ernie.png`}
        alt="Ernie"
        className="ernie-image"
      />
    </div>
  );
}
