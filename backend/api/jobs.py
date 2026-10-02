"""Bounded, cancellable execution of design runs off the request path.

Measured on the demo case before this module existed: the deterministic engine
enumerates and ranks in about 0.4 s, while the narrative agent's model loop takes
about 120 s. The synchronous endpoint therefore held an HTTP worker for two
minutes per run almost entirely waiting on a model, and the facts an engineer
needs were hostage to prose they may not need.

So a run is two stages on two separately bounded pools:

``engine``
    Deterministic enumeration, empirical overlay, and persistence of the facts.
    When this stage finishes the design exists and can be read, whatever happens
    to the narrative afterwards.
``narrative``
    The optional agent layer (framework 6A.3 places it after the deterministic
    path, so it is severable). A slow or unavailable model delays only the
    narrative of the run that asked for it, never another perimeter's facts.

This module knows nothing about databases or perimeters beyond an opaque key
used for admission control. The API layer owns persistence and tells the
runner what to execute.
"""
from __future__ import annotations

import threading
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field

STAGE_ENGINE = "engine"
STAGE_NARRATIVE = "narrative"
STAGES = (STAGE_ENGINE, STAGE_NARRATIVE)


class AdmissionRefused(Exception):
    """The run queue is full, globally or for one perimeter.

    Carries a caller-facing message and a retry hint. Refusing at admission is
    the point of the queue: an unbounded queue only moves the exhaustion from
    HTTP workers to memory and wait time.
    """

    def __init__(self, message: str, retry_after_s: int) -> None:
        super().__init__(message)
        self.retry_after_s = retry_after_s


class JobCancelled(Exception):
    """Raised inside a stage when its job was cancelled."""


@dataclass(frozen=True)
class RunnerLimits:
    engine_workers: int = 2
    narrative_workers: int = 4
    # Jobs admitted but not yet terminal, across all perimeters.
    max_active_jobs: int = 32
    # Jobs admitted but not yet terminal, for one perimeter. Stops one tenant
    # from occupying the whole queue.
    max_active_jobs_per_perimeter: int = 4

    def __post_init__(self) -> None:
        for name in (
            "engine_workers",
            "narrative_workers",
            "max_active_jobs",
            "max_active_jobs_per_perimeter",
        ):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} must be at least 1.")


@dataclass
class _Handle:
    job_id: str
    perimeter_key: str
    cancel: threading.Event = field(default_factory=threading.Event)
    done: threading.Event = field(default_factory=threading.Event)
    future: Future | None = None
    stage: str = STAGE_ENGINE


class JobContext:
    """What a running stage may see: its id and whether it should stop."""

    def __init__(self, handle: _Handle) -> None:
        self._handle = handle

    @property
    def job_id(self) -> str:
        return self._handle.job_id

    def cancelled(self) -> bool:
        return self._handle.cancel.is_set()

    def raise_if_cancelled(self) -> None:
        if self._handle.cancel.is_set():
            raise JobCancelled(self._handle.job_id)


StageFn = Callable[[JobContext], None]


class JobRunner:
    """Two bounded thread pools with admission control.

    Threads rather than processes, on measurement: the CPU-bound engine stage is
    sub-second on the demo case, and the long stage is I/O-bound on a model
    endpoint, where the GIL is released. A process pool would add spawn cost and
    pickling of Case objects (whose generic Tracked[...] fields do not pickle)
    for no gain at these durations. Revisit if engine runs grow past seconds.
    """

    def __init__(self, limits: RunnerLimits | None = None) -> None:
        self.limits = limits or RunnerLimits()
        self._pools = {
            STAGE_ENGINE: ThreadPoolExecutor(
                max_workers=self.limits.engine_workers, thread_name_prefix="esp-engine"
            ),
            STAGE_NARRATIVE: ThreadPoolExecutor(
                max_workers=self.limits.narrative_workers,
                thread_name_prefix="esp-narrative",
            ),
        }
        self._lock = threading.Lock()
        self._handles: dict[str, _Handle] = {}
        self._closed = False

    # -- admission ---------------------------------------------------------

    def admit(self, job_id: str, perimeter_key: str) -> None:
        """Reserve a slot for ``job_id`` or raise ``AdmissionRefused``."""
        with self._lock:
            if self._closed:
                raise AdmissionRefused("The run queue is shutting down.", retry_after_s=30)
            active = len(self._handles)
            mine = sum(1 for h in self._handles.values() if h.perimeter_key == perimeter_key)
            if mine >= self.limits.max_active_jobs_per_perimeter:
                raise AdmissionRefused(
                    f"This perimeter already has {mine} design run(s) queued or running, "
                    f"the per-perimeter limit is {self.limits.max_active_jobs_per_perimeter}. "
                    "Wait for one to finish or cancel one.",
                    retry_after_s=15,
                )
            if active >= self.limits.max_active_jobs:
                raise AdmissionRefused(
                    f"The design-run queue is full ({active} active, limit "
                    f"{self.limits.max_active_jobs}). Retry shortly.",
                    retry_after_s=30,
                )
            self._handles[job_id] = _Handle(job_id=job_id, perimeter_key=perimeter_key)

    # -- execution ---------------------------------------------------------

    def submit(self, job_id: str, stage: str, fn: StageFn) -> None:
        """Queue ``fn`` on ``stage``'s pool. The job must have been admitted.

        A stage that wants a follow-on stage calls ``submit`` again for the same
        job; the job stays admitted until ``finish`` is called.
        """
        if stage not in self._pools:
            raise ValueError(f"Unknown stage {stage!r}.")
        with self._lock:
            handle = self._handles.get(job_id)
            if handle is None:
                raise KeyError(f"Job {job_id} was not admitted.")
            handle.stage = stage
            context = JobContext(handle)
            handle.future = self._pools[stage].submit(fn, context)

    def finish(self, job_id: str) -> None:
        """Release the job's slot and wake anyone waiting on it."""
        with self._lock:
            handle = self._handles.pop(job_id, None)
        if handle is not None:
            handle.done.set()

    # -- control and inspection -------------------------------------------

    def is_live(self, job_id: str) -> bool:
        """True while this process holds the job. False after a restart."""
        with self._lock:
            return job_id in self._handles

    def cancel_requested(self, job_id: str) -> bool:
        """True between a cancel request and the running stage acknowledging it."""
        with self._lock:
            handle = self._handles.get(job_id)
            return bool(handle and handle.cancel.is_set())

    def stage_of(self, job_id: str) -> str | None:
        with self._lock:
            handle = self._handles.get(job_id)
            return handle.stage if handle else None

    def cancel(self, job_id: str) -> bool:
        """Request cancellation. Returns True if the job was still queued.

        A queued stage is removed from its pool and never runs; the caller must
        then record the cancellation itself because no stage code will. A
        running stage is signalled and stops at its next check.
        """
        with self._lock:
            handle = self._handles.get(job_id)
            if handle is None:
                return False
            handle.cancel.set()
            future = handle.future
        return bool(future is not None and future.cancel())

    def wait(self, job_id: str, timeout_s: float) -> bool:
        """Block until the job finishes. True if it finished within the timeout."""
        with self._lock:
            handle = self._handles.get(job_id)
        if handle is None:
            return True
        return handle.done.wait(timeout_s)

    def stats(self) -> dict[str, int]:
        with self._lock:
            by_stage = {stage: 0 for stage in STAGES}
            for handle in self._handles.values():
                by_stage[handle.stage] = by_stage.get(handle.stage, 0) + 1
            return {
                "active_jobs": len(self._handles),
                "engine_stage_jobs": by_stage[STAGE_ENGINE],
                "narrative_stage_jobs": by_stage[STAGE_NARRATIVE],
            }

    def shutdown(self) -> None:
        """Stop accepting work, cancel everything queued, signal everything running."""
        with self._lock:
            self._closed = True
            handles = list(self._handles.values())
        for handle in handles:
            handle.cancel.set()
        for pool in self._pools.values():
            pool.shutdown(wait=False, cancel_futures=True)
