"""The camera's power state from the wake service, read without waking it."""

from __future__ import annotations

import asyncio
import threading
import time
from collections.abc import Callable

from .wakeup import WakeCredentials, WakeError, query_power_state


PowerQuery = Callable[[], "str | None"]
LogSink = Callable[[str], None]

# Power states in which a battery camera is not drawing its awake current.
ASLEEP = frozenset({"sleeping", "deep_sleep", "micro_power"})
# Seconds after a live session ends at which to check that the camera slept.
SLEEP_CHECKS = (15.0, 30.0, 60.0, 120.0, 240.0)


def wake_service_query(did: str, credentials: WakeCredentials) -> PowerQuery:
    def query() -> str | None:
        try:
            return asyncio.run(query_power_state(did, credentials))
        except (WakeError, OSError):
            return None

    return query


class PowerMonitor:
    """A cached power state, refreshed in the background.

    ``current`` never blocks on the network: Home Assistant asks for the camera
    status often, and the wake service only needs asking every few minutes.
    """

    def __init__(
        self,
        name: str,
        query: PowerQuery,
        *,
        ttl: float = 300.0,
        log: LogSink | None = None,
        sleep_checks: tuple[float, ...] = SLEEP_CHECKS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._name = name
        self._query = query
        self._ttl = ttl
        self._log = log
        self._sleep_checks = sleep_checks
        self._clock = clock
        self._lock = threading.Lock()
        self._state: str | None = None
        self._checked_at: float | None = None
        self._refreshing = False
        self._verifying = False

    def current(self) -> str | None:
        with self._lock:
            stale = self._checked_at is None or self._clock() - self._checked_at >= self._ttl
            if stale and not self._refreshing and not self._verifying:
                self._refreshing = True
                threading.Thread(target=self._refresh, daemon=True).start()
            return self._state

    def _refresh(self) -> None:
        state = self._query()
        with self._lock:
            self._record(state)
            self._refreshing = False

    def _record(self, state: str | None) -> None:
        self._checked_at = self._clock()
        if state is not None:
            self._state = state

    def verify_sleep(self) -> None:
        """After a live session, log when the camera reports sleeping again."""

        with self._lock:
            if self._verifying:
                return
            self._verifying = True
            # The camera was awake for the session.
            self._state = "awake"
        threading.Thread(target=self._verify, daemon=True).start()

    def _verify(self) -> None:
        elapsed = 0.0
        state: str | None = None
        try:
            for check in self._sleep_checks:
                time.sleep(max(0.0, check - elapsed))
                elapsed = check
                state = self._query()
                with self._lock:
                    self._record(state)
                if state in ASLEEP:
                    self._emit(f"camera_power camera={self._name} state={state} "
                               f"after_seconds={int(elapsed)} asleep=true")
                    return
            self._emit(f"camera_power camera={self._name} state={state or 'unknown'} "
                       f"after_seconds={int(elapsed)} asleep=false")
        finally:
            with self._lock:
                self._verifying = False

    def _emit(self, line: str) -> None:
        if self._log is not None:
            self._log(line)
