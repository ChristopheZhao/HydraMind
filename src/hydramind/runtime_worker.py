"""Queue execution host for RuntimeSession ids."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any, Protocol

from pydantic import BaseModel, ConfigDict, Field

from hydramind.control import RuntimeDecision
from hydramind.queue import QueueAdapter, QueueCapabilityError, QueueMessage
from hydramind.runtime_worker_readiness import (
    WorkerReadinessSnapshot,
    worker_readiness,
)

__all__ = [
    "QueueExecutionHost",
    "SessionOrchestrator",
    "WorkerDeliveryAction",
    "WorkerHealthSnapshot",
    "WorkerLoopExitContract",
    "WorkerLoopResult",
    "WorkerLoopStopReason",
    "WorkerReadinessSnapshot",
    "WorkerRunResult",
    "worker_loop_exit_contract",
    "worker_readiness",
]


class WorkerDeliveryAction(StrEnum):
    """Queue delivery action taken by one worker polling cycle."""

    IDLE = "idle"
    ACK = "ack"
    NACK_RETRY = "nack_retry"
    NACK_DROP = "nack_drop"


class WorkerLoopStopReason(StrEnum):
    """Why a bounded worker loop stopped."""

    MAX_ITERATIONS = "max_iterations"
    MAX_IDLE_CYCLES = "max_idle_cycles"
    STOP_REQUESTED = "stop_requested"


class WorkerLoopExitContract(BaseModel):
    """Process-supervisor exit evidence derived from worker loop results."""

    model_config = ConfigDict(frozen=True)

    exit_code: int
    restart_recommended: bool


class WorkerRunResult(BaseModel):
    """Outcome of one worker polling cycle."""

    model_config = ConfigDict(frozen=True)

    status: str
    session_id: str | None = None
    decision_kind: str | None = None
    node_key: str | None = None
    queue_attempt: int | None = None
    worker_id: str | None = None
    queue_name: str | None = None
    message_handle: str | None = None
    delivery_action: WorkerDeliveryAction | None = None
    started_at: datetime | None = None
    finished_at: datetime | None = None
    duration_ms: float | None = None
    retry_on_error: bool | None = None
    error_type: str | None = None
    error: str | None = None


class WorkerLoopResult(BaseModel):
    """Aggregate evidence for a bounded worker polling loop."""

    model_config = ConfigDict(frozen=True)

    worker_id: str
    queue_name: str
    stop_reason: WorkerLoopStopReason
    iterations: int = 0
    deliveries: int = 0
    acked: int = 0
    nack_retried: int = 0
    nack_dropped: int = 0
    errors: int = 0
    idle_cycles: int = 0
    consecutive_idle_cycles: int = 0
    last_result: WorkerRunResult | None = None
    exit_code: int
    restart_recommended: bool
    started_at: datetime
    finished_at: datetime
    duration_ms: float


class WorkerHealthSnapshot(BaseModel):
    """Read-only worker liveness snapshot."""

    model_config = ConfigDict(frozen=True)

    worker_id: str
    queue_name: str
    pending: int
    in_flight: int | None = None
    dead_letters: int | None = None
    checked_at: datetime = Field(default_factory=lambda: datetime.now(UTC))


class SessionOrchestrator(Protocol):
    """Worker-compatible orchestrator surface."""

    async def run_session(
        self,
        session_id: str,
        *,
        execution_owner: str | None = None,
        lease_ttl_seconds: int = 300,
        lease_heartbeat_interval_seconds: float | None = None,
    ) -> RuntimeDecision: ...


class QueueExecutionHost:
    """Consume queued session ids and drive them through OrchestratorAgent."""

    def __init__(
        self,
        *,
        queue: QueueAdapter,
        orchestrator: SessionOrchestrator,
        worker_id: str = "queue-worker",
        lease_ttl_seconds: int = 300,
        lease_heartbeat_interval_seconds: float | None = None,
    ) -> None:
        _require_pollable_delivery(queue)
        self._queue = queue
        self._orchestrator = orchestrator
        self._worker_id = worker_id
        self._lease_ttl_seconds = lease_ttl_seconds
        self._lease_heartbeat_interval_seconds = lease_heartbeat_interval_seconds

    async def run_once(
        self,
        *,
        timeout: float | None = None,
        retry_on_error: bool = True,
    ) -> WorkerRunResult:
        started_at = _now()
        message = await self._queue.dequeue(timeout=timeout)
        if message is None:
            return _finish_result(
                status="idle",
                worker_id=self._worker_id,
                queue_name=self._queue.name,
                delivery_action=WorkerDeliveryAction.IDLE,
                started_at=started_at,
            )
        try:
            decision = await self._orchestrator.run_session(
                message.session_id,
                execution_owner=self._worker_id,
                lease_ttl_seconds=self._lease_ttl_seconds,
                lease_heartbeat_interval_seconds=self._lease_heartbeat_interval_seconds,
            )
        except Exception as exc:
            await self._queue.nack(message, retry=retry_on_error)
            return _result_from_error(
                message,
                exc,
                worker_id=self._worker_id,
                queue_name=self._queue.name,
                retry_on_error=retry_on_error,
                started_at=started_at,
            )
        await self._queue.ack(message)
        return _finish_result(
            status=_status_from_decision(decision.kind.value),
            session_id=message.session_id,
            decision_kind=decision.kind.value,
            node_key=decision.node_key,
            queue_attempt=message.attempt,
            worker_id=self._worker_id,
            queue_name=self._queue.name,
            message_handle=message.handle,
            delivery_action=WorkerDeliveryAction.ACK,
            started_at=started_at,
        )

    async def run_loop(
        self,
        *,
        timeout: float | None = None,
        retry_on_error: bool = True,
        max_iterations: int | None = None,
        max_idle_cycles: int | None = None,
        stop_requested: Callable[[], bool] | None = None,
    ) -> WorkerLoopResult:
        """Poll repeatedly through ``run_once`` until a configured bound stops.

        This is the library primitive behind long-running workers. It preserves
        the one-message delivery path and records aggregate loop evidence without
        storing unbounded per-message history.
        """

        _validate_positive_limit("max_iterations", max_iterations)
        _validate_positive_limit("max_idle_cycles", max_idle_cycles)

        started_at = _now()
        iterations = 0
        deliveries = 0
        acked = 0
        nack_retried = 0
        nack_dropped = 0
        errors = 0
        idle_cycles = 0
        consecutive_idle_cycles = 0
        last_result: WorkerRunResult | None = None

        while True:
            if stop_requested is not None and stop_requested():
                return _finish_loop_result(
                    worker_id=self._worker_id,
                    queue_name=self._queue.name,
                    stop_reason=WorkerLoopStopReason.STOP_REQUESTED,
                    iterations=iterations,
                    deliveries=deliveries,
                    acked=acked,
                    nack_retried=nack_retried,
                    nack_dropped=nack_dropped,
                    errors=errors,
                    idle_cycles=idle_cycles,
                    consecutive_idle_cycles=consecutive_idle_cycles,
                    last_result=last_result,
                    started_at=started_at,
                )

            result = await self.run_once(
                timeout=timeout,
                retry_on_error=retry_on_error,
            )
            last_result = result
            iterations += 1

            if result.delivery_action is WorkerDeliveryAction.IDLE:
                idle_cycles += 1
                consecutive_idle_cycles += 1
            else:
                deliveries += 1
                consecutive_idle_cycles = 0
                if result.delivery_action is WorkerDeliveryAction.ACK:
                    acked += 1
                elif result.delivery_action is WorkerDeliveryAction.NACK_RETRY:
                    nack_retried += 1
                elif result.delivery_action is WorkerDeliveryAction.NACK_DROP:
                    nack_dropped += 1
                if result.status == "error":
                    errors += 1

            if stop_requested is not None and stop_requested():
                return _finish_loop_result(
                    worker_id=self._worker_id,
                    queue_name=self._queue.name,
                    stop_reason=WorkerLoopStopReason.STOP_REQUESTED,
                    iterations=iterations,
                    deliveries=deliveries,
                    acked=acked,
                    nack_retried=nack_retried,
                    nack_dropped=nack_dropped,
                    errors=errors,
                    idle_cycles=idle_cycles,
                    consecutive_idle_cycles=consecutive_idle_cycles,
                    last_result=last_result,
                    started_at=started_at,
                )

            if (
                max_iterations is not None
                and iterations >= max_iterations
            ):
                return _finish_loop_result(
                    worker_id=self._worker_id,
                    queue_name=self._queue.name,
                    stop_reason=WorkerLoopStopReason.MAX_ITERATIONS,
                    iterations=iterations,
                    deliveries=deliveries,
                    acked=acked,
                    nack_retried=nack_retried,
                    nack_dropped=nack_dropped,
                    errors=errors,
                    idle_cycles=idle_cycles,
                    consecutive_idle_cycles=consecutive_idle_cycles,
                    last_result=last_result,
                    started_at=started_at,
                )

            if (
                max_idle_cycles is not None
                and consecutive_idle_cycles >= max_idle_cycles
            ):
                return _finish_loop_result(
                    worker_id=self._worker_id,
                    queue_name=self._queue.name,
                    stop_reason=WorkerLoopStopReason.MAX_IDLE_CYCLES,
                    iterations=iterations,
                    deliveries=deliveries,
                    acked=acked,
                    nack_retried=nack_retried,
                    nack_dropped=nack_dropped,
                    errors=errors,
                    idle_cycles=idle_cycles,
                    consecutive_idle_cycles=consecutive_idle_cycles,
                    last_result=last_result,
                    started_at=started_at,
                )

    async def health(self) -> WorkerHealthSnapshot:
        """Return queue liveness without touching RuntimeSession state."""

        pending = await self._queue.pending()
        in_flight = await _optional_count(self._queue, "in_flight")
        dead_letters = await _optional_dead_letter_count(self._queue)
        return WorkerHealthSnapshot(
            worker_id=self._worker_id,
            queue_name=self._queue.name,
            pending=pending,
            in_flight=in_flight,
            dead_letters=dead_letters,
        )


def worker_loop_exit_contract(result: WorkerLoopResult) -> WorkerLoopExitContract:
    """Return the process-supervisor contract for a completed worker loop."""

    return _exit_contract_for_errors(result.errors)


def _result_from_error(
    message: QueueMessage,
    exc: Exception,
    *,
    worker_id: str,
    queue_name: str,
    retry_on_error: bool,
    started_at: datetime,
) -> WorkerRunResult:
    return _finish_result(
        status="error",
        session_id=message.session_id,
        queue_attempt=message.attempt,
        worker_id=worker_id,
        queue_name=queue_name,
        message_handle=message.handle,
        delivery_action=(
            WorkerDeliveryAction.NACK_RETRY
            if retry_on_error
            else WorkerDeliveryAction.NACK_DROP
        ),
        started_at=started_at,
        retry_on_error=retry_on_error,
        error_type=type(exc).__name__,
        error=str(exc) or type(exc).__name__,
    )


def _finish_result(*, started_at: datetime, **kwargs: Any) -> WorkerRunResult:
    finished_at = _now()
    return WorkerRunResult(
        **kwargs,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=(finished_at - started_at).total_seconds() * 1000,
    )


def _finish_loop_result(
    *,
    worker_id: str,
    queue_name: str,
    stop_reason: WorkerLoopStopReason,
    iterations: int,
    deliveries: int,
    acked: int,
    nack_retried: int,
    nack_dropped: int,
    errors: int,
    idle_cycles: int,
    consecutive_idle_cycles: int,
    last_result: WorkerRunResult | None,
    started_at: datetime,
) -> WorkerLoopResult:
    finished_at = _now()
    exit_contract = _exit_contract_for_errors(errors)
    return WorkerLoopResult(
        worker_id=worker_id,
        queue_name=queue_name,
        stop_reason=stop_reason,
        iterations=iterations,
        deliveries=deliveries,
        acked=acked,
        nack_retried=nack_retried,
        nack_dropped=nack_dropped,
        errors=errors,
        idle_cycles=idle_cycles,
        consecutive_idle_cycles=consecutive_idle_cycles,
        last_result=last_result,
        exit_code=exit_contract.exit_code,
        restart_recommended=exit_contract.restart_recommended,
        started_at=started_at,
        finished_at=finished_at,
        duration_ms=(finished_at - started_at).total_seconds() * 1000,
    )


def _exit_contract_for_errors(errors: int) -> WorkerLoopExitContract:
    if errors > 0:
        return WorkerLoopExitContract(exit_code=1, restart_recommended=True)
    return WorkerLoopExitContract(exit_code=0, restart_recommended=False)


def _validate_positive_limit(name: str, value: int | None) -> None:
    if value is not None and value <= 0:
        raise ValueError(f"{name} must be positive")


async def _optional_count(queue: QueueAdapter, method_name: str) -> int | None:
    method = getattr(queue, method_name, None)
    if not callable(method):
        return None
    value = await method()
    return int(value)


async def _optional_dead_letter_count(queue: QueueAdapter) -> int | None:
    method = getattr(queue, "dead_letters", None)
    if not callable(method):
        return None
    value = await method()
    return len(value)


def _require_pollable_delivery(queue: QueueAdapter) -> None:
    capabilities = queue.capabilities
    if capabilities.supports_pollable_delivery:
        return
    raise QueueCapabilityError(
        f"{queue.name!r} queue does not support pollable delivery; "
        "QueueExecutionHost requires dequeue, ack, and nack capabilities. "
        "Use InMemoryQueueAdapter for local polling or provide a broker adapter "
        "that declares QueueCapabilities.pollable_delivery()."
    )


def _now() -> datetime:
    return datetime.now(UTC)


def _status_from_decision(decision_kind: str) -> str:
    if decision_kind == "complete":
        return "completed"
    if decision_kind == "await_gate":
        return "waiting_gate"
    if decision_kind == "fail":
        return "failed"
    return decision_kind
