import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { CallPanel } from "./components/CallPanel";
import { Notebook } from "./components/Notebook";
import { api, LiveConnection } from "./lib/api";
import { AudioPlayer, MicCapture } from "./lib/audio";
import { CameraStream, fileToJpegBase64 } from "./lib/camera";
import { initialState, reducer } from "./lib/state";
import type { ServerMessage } from "./lib/types";

const STORAGE_KEY = "claimvoice.claimId";

function remember(id: string | null) {
  try {
    if (id) sessionStorage.setItem(STORAGE_KEY, id);
    else sessionStorage.removeItem(STORAGE_KEY);
  } catch {
    // storage unavailable (private mode): resume just won't work
  }
}

function recall(): string | null {
  try {
    return sessionStorage.getItem(STORAGE_KEY);
  } catch {
    return null;
  }
}

export default function App() {
  const [state, dispatch] = useReducer(reducer, initialState);
  const [claimId, setClaimId] = useState<string | null>(null);
  const [micOn, setMicOn] = useState(false);
  const live = useRef<LiveConnection | null>(null);
  const mic = useRef<MicCapture | null>(null);
  const player = useRef(new AudioPlayer());
  const camera = useRef<CameraStream | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [cameraOn, setCameraOn] = useState(false);

  const loadClaim = useCallback(async (resume: boolean) => {
    try {
      const saved = resume ? recall() : null;
      const claim = saved ? await api.getClaim(saved).catch(() => api.createClaim()) : await api.createClaim();
      setClaimId(claim.id);
      remember(claim.id);
      dispatch({ type: "claim", claim: claim.state });
    } catch (e) {
      dispatch({ type: "error", error: `Could not reach the server. ${(e as Error).message}` });
    }
  }, []);

  const started = useRef(false);
  useEffect(() => {
    if (started.current) return; // React dev mode runs effects twice; create one claim, not two
    started.current = true;
    void loadClaim(true);
  }, [loadClaim]);

  const stopMic = () => {
    mic.current?.stop();
    mic.current = null;
    setMicOn(false);
  };

  const stopCamera = useCallback((notify: boolean) => {
    camera.current?.stop(videoRef.current);
    camera.current = null;
    setCameraOn(false);
    if (notify) live.current?.send({ type: "camera", enabled: false });
  }, []);

  const endCall = useCallback(() => {
    stopCamera(true);
    stopMic();
    live.current?.send({ type: "audio_end" });
    live.current?.close();
    live.current = null;
    player.current.stop();
    dispatch({ type: "call", status: "ended" });
  }, [stopCamera]);

  const onServerMessage = useCallback((message: ServerMessage) => {
    if (message.type === "audio") player.current.play(message.data);
    else if (message.type === "interrupted") player.current.stop();
    dispatch({ type: "server", message });
    if (message.type === "ready") {
      const capture = new MicCapture();
      capture
        .start((chunk) => live.current?.send({ type: "audio", data: chunk }))
        .then(() => {
          mic.current = capture;
          setMicOn(true);
        })
        .catch(() => {
          capture.stop();
          dispatch({ type: "error", error: "Microphone unavailable. You can still type; the agent will answer out loud." });
        });
    }
  }, []);

  const startCall = async () => {
    if (!claimId || live.current) return;
    await player.current.unlock(); // inside the click handler, so the browser allows audio
    dispatch({ type: "error", error: "" });
    dispatch({ type: "call", status: "connecting" });
    live.current = new LiveConnection(claimId, onServerMessage, (reason) => {
      stopCamera(false);
      stopMic();
      player.current.stop();
      live.current = null;
      dispatch({ type: "call", status: "ended" });
      if (reason) dispatch({ type: "error", error: reason });
    });
  };

  const send = async (text: string) => {
    if (!claimId) return;
    const id = crypto.randomUUID();
    if (live.current?.open) {
      live.current.send({ type: "text", text, id });
      return;
    }
    // Typed mode: no live call needed. The agent replies with the next question.
    dispatch({ type: "server", message: { type: "transcript", speaker: "claimant", id, text, final: false } });
    dispatch({ type: "busy", busy: true });
    try {
      const result = await api.sendMessage(claimId, text, id);
      dispatch({ type: "claim", claim: result.state });
      dispatch({ type: "error", error: "" });
    } catch (e) {
      dispatch({ type: "error", error: (e as Error).message });
    } finally {
      dispatch({ type: "busy", busy: false });
    }
  };

  const toggleCamera = async () => {
    if (camera.current) {
      stopCamera(true);
      return;
    }
    if (!live.current?.open || !videoRef.current) return;
    const stream = new CameraStream();
    try {
      // Mark the camera on first so the server accepts the frames that follow.
      live.current.send({ type: "camera", enabled: true });
      setCameraOn(true);
      await stream.start(
        videoRef.current,
        (frame) => live.current?.send({ type: "video", data: frame }),
        () => stopCamera(true),
      );
      camera.current = stream;
    } catch {
      stream.stop(videoRef.current);
      stopCamera(true);
      dispatch({ type: "error", error: "Camera unavailable. You can upload a photo instead." });
    }
  };

  const capturePhoto = () => live.current?.send({ type: "capture", claim: "" });

  const uploadPhoto = async (file: File) => {
    if (!claimId) return;
    dispatch({ type: "busy", busy: true });
    try {
      const result = await api.uploadEvidence(claimId, await fileToJpegBase64(file), "");
      dispatch({ type: "claim", claim: result.state });
      dispatch({ type: "error", error: "" });
    } catch (e) {
      dispatch({ type: "error", error: `Photo upload failed. ${(e as Error).message}` });
    } finally {
      dispatch({ type: "busy", busy: false });
    }
  };

  const newClaim = async () => {
    endCall();
    if (claimId) await api.deleteClaim(claimId).catch(() => undefined);
    remember(null);
    dispatch({ type: "call", status: "idle" });
    await loadClaim(false);
  };

  useEffect(
    () => () => {
      if (live.current) endCall(); // leaving the page ends an active call and releases the mic
    },
    [endCall],
  );

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <span className="logo" aria-hidden>
            ◉
          </span>
          ClaimVoice
          <span className="muted">Report a claim by talking</span>
        </div>
        <div className="actions">
          {claimId && state.claim?.packet_markdown && (
            <a className="btn" href={api.packetUrl(claimId)} download>
              Download packet
            </a>
          )}
          <button className="btn" onClick={newClaim}>
            New claim
          </button>
        </div>
      </header>

      {state.error && (
        <p className="banner banner-warn app-error" role="alert">
          {state.error}
        </p>
      )}

      <main className="layout">
        <CallPanel
          transcript={state.transcript}
          call={state.call}
          busy={state.busy}
          micOn={micOn}
          cameraOn={cameraOn}
          videoRef={videoRef}
          onStart={startCall}
          onEnd={endCall}
          onSend={send}
          onCamera={toggleCamera}
          onCapture={capturePhoto}
          onUpload={uploadPhoto}
        />
        <Notebook claim={state.claim} tools={state.tools} />
      </main>
    </div>
  );
}
