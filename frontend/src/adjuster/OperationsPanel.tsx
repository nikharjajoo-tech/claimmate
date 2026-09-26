import { useEffect, useState } from "react";
import { adjusterApi, formatMs, type OperationsMetrics } from "../lib/adjuster";

export function OperationsPanel({ onUnauthorized }: { onUnauthorized: (e: unknown) => void }) {
  const [metrics, setMetrics] = useState<OperationsMetrics | null>(null);
  const [error, setError] = useState("");

  useEffect(() => {
    adjusterApi.metrics().then(setMetrics, (e) => {
      setError("Could not load metrics.");
      onUnauthorized(e);
    });
  }, [onUnauthorized]);

  if (!metrics) return <section className="panel">{error || "Loading metrics…"}</section>;
  const { claims, pipeline, voice, review } = metrics;
  const voiceMet = voice.first_audio_p50_ms !== null && voice.first_audio_p50_ms <= voice.target_p50_ms;

  return (
    <section className="panel operations" aria-label="Operations">
      <h2>Operations</h2>
      <p className="muted">Live figures from stored claims since this database was created.</p>
      <div className="metric-grid">
        <div className="metric">
          <span className="metric-label">Voice reply latency (p50)</span>
          <span className="metric-value">{formatMs(voice.first_audio_p50_ms)}</span>
          <span className={`metric-note ${voice.first_audio_p50_ms === null ? "" : voiceMet ? "ok-text" : "warn-text"}`}>
            Target ≤ {formatMs(voice.target_p50_ms)} · p95 {formatMs(voice.first_audio_p95_ms)} · {voice.turns} replies
          </span>
        </div>
        <div className="metric">
          <span className="metric-label">Claim pipeline latency (p50)</span>
          <span className="metric-value">{formatMs(pipeline.latency_p50_ms)}</span>
          <span className="metric-note">p95 {formatMs(pipeline.latency_p95_ms)} · {pipeline.runs} runs</span>
        </div>
        <div className="metric">
          <span className="metric-label">Claims</span>
          <span className="metric-value">{claims.total}</span>
          <span className="metric-note">
            {Object.entries(claims.by_status).map(([s, n]) => `${n} ${s.replace("_", " ")}`).join(" · ") || "None yet"}
          </span>
        </div>
        <div className="metric">
          <span className="metric-label">Adjuster overrides</span>
          <span className="metric-value">
            {review.overridden}
            <span className="muted"> / {review.reviewed} reviewed</span>
          </span>
          <span className="metric-note">How often people disagree with the automatic route</span>
        </div>
        <div className="metric">
          <span className="metric-label">Tokens used</span>
          <span className="metric-value">{(pipeline.tokens_in + pipeline.tokens_out).toLocaleString()}</span>
          <span className="metric-note">
            {pipeline.tokens_in.toLocaleString()} in · {pipeline.tokens_out.toLocaleString()} out
          </span>
        </div>
        <div className="metric">
          <span className="metric-label">Models used</span>
          <span className="metric-note model-list">
            {Object.entries(pipeline.runs_by_model).map(([m, n]) => `${m} ×${n}`).join(", ") || "None yet"}
          </span>
        </div>
      </div>
    </section>
  );
}
