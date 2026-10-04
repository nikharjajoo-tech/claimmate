import { useCallback, useEffect, useReducer, useRef, useState } from "react";
import { Logo } from "./components/Logo";
import { CallPanel } from "./components/CallPanel";
import { Notebook } from "./components/Notebook";
import { api, LiveConnection } from "./lib/api";
import { AudioPlayer, MicCapture } from "./lib/audio";
import { batchPhotos, CameraStream, fileToJpegBase64 } from "./lib/camera";
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
  const currentClaim = useRef<string | null>(null);
  useEffect(() => {
    currentClaim.current = claimId;
  }, [claimId]);
  const [micOn, setMicOn] = useState(false);
  const live = useRef<LiveConnection | null>(null);
  const mic = useRef<MicCapture | null>(null);
  const player = useRef(new AudioPlayer());
  const camera = useRef<CameraStream | null>(null);
  const videoRef = useRef<HTMLVideoElement>(null);
  const [cameraOn, setCameraOn] = useState(false);
  const [uploading, setUploading] = useState<{ done: number; total: number } | null>(null);

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

  const refreshClaim = useCallback(async (id: string) => {
    try {
      const result = await api.getClaim(id);
      if (currentClaim.current === id) dispatch({ type: "claim", claim: result.state }); // ignore if replaced
    } catch {
      // the next action will surface any problem
    }
  }, []);

  const endCall = useCallback(() => {
    stopCamera(true);
    stopMic();
    live.current?.send({ type: "audio_end" });
    live.current?.close();
    live.current = null;
    player.current.stop();
    dispatch({ type: "call", status: "ended" });
    // Ending the call submits the claim; the server finishes that as the connection closes.
    if (claimId) window.setTimeout(() => void refreshClaim(claimId), 800);
  }, [stopCamera, claimId, refreshClaim]);

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

  // Photos go up in batches, one batch at a time: the server verifies each photo on its own but
  // updates the claim once per batch, and parallel requests would race on the same claim.
  // A photo that fails does not stop the rest.
  const uploadPhotos = async (files: File[]) => {
    if (!claimId) return;
    dispatch({ type: "busy", busy: true });
    setUploading({ done: 0, total: files.length });
    const failed: string[] = [];
    let reason = "";
    const fail = (name: string, message: string) => {
      failed.push(name);
      reason ||= message;
    };
    try {
      const photos: { name: string; data: string; claim: string }[] = [];
      for (const file of files) {
        try {
          photos.push({ name: file.name, data: await fileToJpegBase64(file), claim: "" });
        } catch {
          fail(file.name, "That file could not be read as an image.");
        }
      }
      let done = files.length - photos.length;
      for (const batch of batchPhotos(photos)) {
        setUploading({ done, total: files.length });
        try {
          const result = await api.uploadEvidenceBatch(
            claimId,
            batch.map(({ data, claim }) => ({ data, claim })),
          );
          result.results.forEach((r, i) => r.ok || fail(batch[i].name, r.error ?? "Upload failed."));
          dispatch({ type: "claim", claim: result.state });
        } catch (e) {
          batch.forEach((p) => fail(p.name, (e as Error).message));
        }
        done += batch.length;
      }
      if (!failed.length) dispatch({ type: "error", error: "" });
      else if (files.length === 1) dispatch({ type: "error", error: `Photo upload failed. ${reason}` });
      else
        dispatch({
          type: "error",
          error: `${failed.length} of ${files.length} photos failed (${failed.join(", ")}). ${reason}`,
        });
    } finally {
      setUploading(null);
      dispatch({ type: "busy", busy: false });
    }
  };

  const newClaim = async () => {
    endCall();
    if (claimId) await api.deleteClaim(claimId).catch(() => undefined);
    remember(null);
    dispatch({ type: "reset" }); // nothing from the old claim may leak into the new one
    await loadClaim(false);
  };

  // Leaving the page releases the mic and camera. This is not an explicit "End call", so the
  // claim stays open and the claimant can reconnect (workflow 5).
  useEffect(
    () => () => {
      camera.current?.stop();
      mic.current?.stop();
      live.current?.close();
    },
    [],
  );

  const submitted = !!state.claim && state.claim.status !== "intake";

  return (
    <div className="app">
      <header className="topbar">
        <div className="brand">
          <Logo />
          <span className="brand-name">
            Claim<b>Mate</b>
          </span>
          <span className="muted">Your personal AI claim assistant</span>
        </div>
        <div className="actions">
          <a className="btn" href="#/adjuster">
            Adjuster view
          </a>
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
          submitted={submitted}
          onNewClaim={newClaim}
          cameraOn={cameraOn}
          videoRef={videoRef}
          onStart={startCall}
          onEnd={endCall}
          onSend={send}
          onCamera={toggleCamera}
          onCapture={capturePhoto}
          onUpload={uploadPhotos}
          uploading={uploading}
        />
        <Notebook claim={state.claim} tools={state.tools} />
      </main>
    </div>
  );
}
