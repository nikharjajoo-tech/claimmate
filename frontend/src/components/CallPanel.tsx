import { useEffect, useRef, useState } from "react";
import type { CallStatus } from "../lib/state";
import type { Turn } from "../lib/types";

interface Props {
  transcript: Turn[];
  call: CallStatus;
  busy: boolean;
  micOn: boolean;
  onStart: () => void;
  onEnd: () => void;
  onSend: (text: string) => void;
}

const STATUS_TEXT: Record<CallStatus, string> = {
  idle: "Not connected",
  connecting: "Connecting…",
  live: "Live",
  ended: "Call ended",
};

export function CallPanel({ transcript, call, busy, micOn, onStart, onEnd, onSend }: Props) {
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const live = call === "live" || call === "connecting";

  useEffect(() => {
    endRef.current?.scrollIntoView({ block: "end" });
  }, [transcript]);

  const submit = (event: React.FormEvent) => {
    event.preventDefault();
    const text = draft.trim();
    if (!text || busy) return;
    onSend(text);
    setDraft("");
  };

  return (
    <section className="panel call" aria-label="Call">
      <header className="call-head">
        <div className={`status status-${call}`} role="status">
          <span className="dot" aria-hidden />
          {STATUS_TEXT[call]}
          {call === "live" && (micOn ? " · listening" : " · mic off")}
        </div>
        {live ? (
          <button className="btn btn-danger" onClick={onEnd}>
            End call
          </button>
        ) : (
          <button className="btn btn-primary" onClick={onStart}>
            Talk
          </button>
        )}
      </header>

      <ol className="transcript" aria-live="polite">
        {transcript.map((turn) => (
          <li key={turn.id} className={`turn turn-${turn.speaker}${turn.partial ? " partial" : ""}`}>
            <span className="who">{turn.speaker === "agent" ? "Agent" : "You"}</span>
            <p>{turn.text}</p>
          </li>
        ))}
        {busy && (
          <li className="turn turn-agent partial">
            <span className="who">Agent</span>
            <p>Updating your claim…</p>
          </li>
        )}
        <div ref={endRef} />
      </ol>

      <form className="composer" onSubmit={submit}>
        <input
          value={draft}
          onChange={(e) => setDraft(e.target.value)}
          placeholder={live ? "Type instead of speaking…" : "Describe what happened, or press Talk"}
          aria-label="Message"
          maxLength={4000}
        />
        <button className="btn" type="submit" disabled={!draft.trim() || busy}>
          Send
        </button>
      </form>
    </section>
  );
}
