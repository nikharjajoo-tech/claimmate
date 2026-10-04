import { useEffect, useRef, useState } from "react";
import type { CallStatus } from "../lib/state";
import type { Turn } from "../lib/types";

interface Props {
  transcript: Turn[];
  call: CallStatus;
  busy: boolean;
  micOn: boolean;
  submitted: boolean;
  onNewClaim: () => void;
  cameraOn: boolean;
  videoRef: React.RefObject<HTMLVideoElement | null>;
  onStart: () => void;
  onEnd: () => void;
  onSend: (text: string) => void;
  onCamera: () => void;
  onCapture: () => void;
  onUpload: (files: File[]) => void;
  uploading: { done: number; total: number } | null;
}

const STATUS_TEXT: Record<CallStatus, string> = {
  idle: "Not connected",
  connecting: "Connecting…",
  live: "Live",
  ended: "Call ended",
};

export function CallPanel(props: Props) {
  const { transcript, call, busy, micOn, submitted, onNewClaim, cameraOn, videoRef, onStart, onEnd, onSend, onCamera,
    onCapture, onUpload, uploading } = props;
  const [draft, setDraft] = useState("");
  const endRef = useRef<HTMLDivElement>(null);
  const fileRef = useRef<HTMLInputElement>(null);
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
        {submitted ? null : live ? (
          <button className="btn btn-danger" onClick={onEnd}>
            End call
          </button>
        ) : (
          <button className="btn btn-primary" onClick={onStart}>
            Talk
          </button>
        )}
      </header>

      {submitted && (
        <div className="banner banner-ok" role="status">
          <strong>Claim submitted for review.</strong> An adjuster will review it and follow up. You can still
          download the packet.{" "}
          <button className="btn btn-small" onClick={onNewClaim}>
            Start a new claim
          </button>
        </div>
      )}

      <div className="media-bar" hidden={submitted}>
        {call === "live" && (
          <button className="btn btn-small" onClick={onCamera} aria-pressed={cameraOn}>
            {cameraOn ? "Stop camera" : "Show camera"}
          </button>
        )}
        {cameraOn && (
          <button className="btn btn-small btn-primary" onClick={onCapture}>
            Capture photo
          </button>
        )}
        <button className="btn btn-small" onClick={() => fileRef.current?.click()} disabled={busy}>
          Upload photos
        </button>
        <input
          ref={fileRef}
          type="file"
          accept="image/*"
          multiple
          hidden
          onChange={(e) => {
            const files = Array.from(e.target.files ?? []);
            if (files.length) onUpload(files);
            e.target.value = "";
          }}
        />
      </div>
      <video ref={videoRef} className="preview" hidden={!cameraOn} muted playsInline aria-label="Camera preview" />

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
            <p>
              {!uploading || uploading.total < 2
                ? "Updating your claim…"
                : uploading.done
                  ? `Checking your photos (${uploading.done} of ${uploading.total} done)…`
                  : `Checking your ${uploading.total} photos…`}
            </p>
          </li>
        )}
        <div ref={endRef} />
      </ol>

      <form className="composer" onSubmit={submit} hidden={submitted}>
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
