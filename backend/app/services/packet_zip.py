"""Downloadable claim packet: the adjuster packet, transcript, evidence manifest, and photos."""

from __future__ import annotations

import io
import json
import zipfile

from app.services.sessions import ClaimSession


def build_packet_zip(session: ClaimSession) -> bytes:
    if session.result is None:
        raise ValueError("No claim packet yet.")
    decision = [
        "",
        "## Review status",
        f"- Status: {session.status}",
        f"- Route: {session.effective_route}"
        + (f" (overridden from {session.frozen_route or session.pipeline_route}: {session.override_reason})"
           if session.route_override else ""),
    ]
    transcript = "\n".join(f"**{t.speaker.title()}** [{t.id}]: {t.text}\n" for t in session.turns)
    manifest = [
        {
            "capture_id": c.capture_id,
            "file": f"evidence/{c.capture_id}.jpg",
            "caption": c.caption,
            "claimant_said": c.claimant_claim,
            "confirmed": c.confirmed,
            "counts_as": [t.value for t in c.document_types],
            "source": c.source,
            "captured_at": c.captured_at,
        }
        for c in session.captures
    ]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as archive:
        archive.writestr("claim.md", session.result.packet.markdown + "\n".join(decision) + "\n")
        archive.writestr("transcript.md", f"# Transcript\n\n{transcript}")
        archive.writestr("evidence.json", json.dumps(manifest, indent=2))
        for capture in session.captures:
            image = session.evidence_images.get(capture.capture_id)
            if image:
                archive.writestr(f"evidence/{capture.capture_id}.jpg", image)
    return buffer.getvalue()
