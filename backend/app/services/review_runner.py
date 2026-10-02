"""When the wording review runs (FR-10.2, FR-10.8, FR-10.9, spec section 9.3).

Submission must not wait for a model call, and it must not fail because of one. A claim is handed
to the adjuster queue by `lifecycle.submit`, which is synchronous and is called from the idle
sweeper as well as from the claimant's own "end call", with no request to hang work off. So:

1. submission writes a `pending` row and schedules a background task;
2. the task loads the claim through the repository, because the submitting code has already
   dropped the in-memory session;
3. if the process dies mid-generation the row stays `pending`, and the next adjuster open of a
   claim pending for longer than the grace period starts it again;
4. a failure is recorded as `failed`, which is what the panel's Retry button acts on.

At most one generation per claim is ever in flight: a second request joins the running one instead
of paying for a second call, and a ready review cannot be regenerated again within a cooldown.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import time
from collections.abc import Callable

from app.domain.models import ClaimFacts, Classification, PolicyLookup
from app.llm.client import StructuredLLM
from app.review.models import PolicyReview
from app.review.prompts import DEFAULT_PROMPT_VERSION
from app.review.service import generate_policy_review
from app.services.sessions import ClaimSession
from app.storage.repository import ClaimRepository

logger = logging.getLogger(__name__)

# A pending row older than this was almost certainly orphaned by a restart or a crash.
STALE_PENDING_S = 180.0
# How soon a ready review may be regenerated (FR-10.9). Refreshing exists for new documents,
# not for re-rolling the model.
REFRESH_COOLDOWN_S = 60.0


class ReviewRunner:
    """Owns the background generation of wording reviews for a store's claims."""

    def __init__(
        self,
        repo: ClaimRepository | None,
        llm_factory: Callable[[], StructuredLLM],
        *,
        prompt_version: str = DEFAULT_PROMPT_VERSION,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self.repo = repo
        self._llm_factory = llm_factory
        self._llm: StructuredLLM | None = None
        self.prompt_version = prompt_version
        self._clock = clock
        self._running: dict[str, asyncio.Task] = {}
        self._finished_at: dict[str, float] = {}

    # --- scheduling ----------------------------------------------------------

    def in_flight(self, claim_id: str) -> bool:
        task = self._running.get(claim_id)
        return task is not None and not task.done()

    def cooling_down(self, claim_id: str) -> float:
        """Seconds left before this claim's review may be regenerated. 0 when it may run now."""
        finished = self._finished_at.get(claim_id)
        if finished is None:
            return 0.0
        return max(0.0, REFRESH_COOLDOWN_S - (self._clock() - finished))

    def schedule(self, session: ClaimSession) -> bool:
        """Start a generation unless one is already running for this claim. Never raises."""
        if self.repo is None or self.in_flight(session.id):
            return False
        task = asyncio.create_task(self._run(session.id))
        self._running[session.id] = task
        task.add_done_callback(lambda t: self._running.pop(session.id, None) if self._running.get(session.id) is t else None)
        return True

    async def refresh(self, session: ClaimSession) -> bool:
        """The adjuster asked for a new one. False when one is running or the cooldown is active."""
        if self.in_flight(session.id) or self.cooling_down(session.id) > 0:
            return False
        return self.schedule(session)

    async def ensure(self, session: ClaimSession) -> None:
        """Called when an adjuster opens a claim: start a review that is missing, or retry one
        that a restart left pending (spec section 9.3)."""
        if self.repo is None or session.status == "intake" or self.in_flight(session.id):
            return
        state = await self.repo.load_review(session.id)
        if state is None:
            self.schedule(session)
            return
        if state["status"] == "pending":
            age = (await self._now()) - state["started_at"].timestamp()
            if age > STALE_PENDING_S:
                logger.info("wording review for %s was pending %.0fs, starting it again", session.id, age)
                self.schedule(session)

    async def drain(self) -> None:
        """Wait for in-flight generations, for shutdown and for tests."""
        for task in list(self._running.values()):
            with contextlib.suppress(Exception):
                await task

    # --- the work ------------------------------------------------------------

    async def _now(self) -> float:
        from datetime import UTC, datetime

        return datetime.now(UTC).timestamp()

    async def _run(self, claim_id: str) -> None:
        assert self.repo is not None
        session = await self.repo.load(claim_id)
        if session is None:
            logger.warning("wording review: claim %s vanished before it could run", claim_id)
            return
        result = session.result
        await self.repo.review_begin(
            claim_id,
            pipeline_revision=session.result_revision if result else None,
            prompt_version=self.prompt_version,
        )
        try:
            if self._llm is None:
                self._llm = self._llm_factory()
            review = await generate_policy_review(
                self._llm,
                policy=result.policy if result else PolicyLookup(found=False, query="", message="No pipeline run yet."),
                facts=result.facts if result else ClaimFacts(),
                classification=result.classification if result else Classification(),
                turns=list(session.turns),
                captures=list(session.captures),
                prompt_version=self.prompt_version,
                pipeline_revision=session.result_revision if result else None,
            )
        except Exception as exc:  # the claim is already submitted; a review failure never undoes that
            logger.exception("wording review failed for %s", claim_id)
            await self.repo.review_fail(claim_id, f"{type(exc).__name__}: {exc}")
            # No cooldown after a failure: the panel's Retry is the whole point, and refusing it
            # for a minute would strand the adjuster on an error they cannot clear.
        else:
            await self.repo.review_finish(claim_id, review)
            self._finished_at[claim_id] = self._clock()


def review_state_view(state: dict | None, *, running: bool) -> dict:
    """What the adjuster API returns for the panel (FR-10.6: adjuster call sites only).

    `running` and `status` are separate on purpose. A refresh is scheduled before the row is
    marked pending, so reporting the stored status alone would hand the adjuster the previous
    review with nothing to say a new one is on its way. The panel keeps showing the old review
    and says it is being re-read.
    """
    if state is None:
        return {"status": "none", "running": running, "review": None, "error": "", "runs": 0}
    review: PolicyReview | None = state["review"]
    return {
        "status": state["status"],  # pending here means stranded: the next open retries it
        "running": running,
        "review": review.model_dump(mode="json") if review else None,
        "error": state["error"],
        "runs": state["runs"],
        "updated_at": state["updated_at"].isoformat(timespec="seconds"),
    }
