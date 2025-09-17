"""SocketCAN abstraction used throughout dm-tui."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from threading import Lock
from typing import Callable, Iterable, Optional

try:
    import can  # type: ignore
except ImportError as exc:  # pragma: no cover - python-can should be installed.
    can = None  # type: ignore[assignment]

    class _BaseListener:  # type: ignore[too-many-ancestors]
        """Fallback listener placeholder when python-can is unavailable."""

        def __init__(self, *_, **__):  # noqa: D401 - compatibility shim
            raise RuntimeError(
                "python-can is required for BusManager; install the 'python-can' package."
            ) from exc

    _IMPORT_ERROR = exc
else:  # pragma: no branch
    _BaseListener = can.Listener  # type: ignore[assignment]
    _IMPORT_ERROR = None

LOGGER = logging.getLogger(__name__)

MessageCallback = Callable[["can.Message"], None]


class BusManagerError(RuntimeError):
    """Raised when bus operations fail."""


@dataclass(slots=True)
class PeriodicTask:
    """Convenience wrapper around python-can periodic tasks."""

    _task: "can.CyclicTaskABC"

    def update(self, *, data: bytes | None = None, period: float | None = None) -> None:
        if data is not None:
            self._task.modify_data(data)
        if period is not None:
            self._task.modify_period(period)

    def stop(self) -> None:
        self._task.stop()


class _CallbackListener(_BaseListener):  # type: ignore[misc]
    """Fan messages out to registered callbacks."""

    def __init__(self) -> None:
        super().__init__()
        self._callbacks: list[MessageCallback] = []
        self._lock = Lock()

    def on_message_received(self, msg: "can.Message") -> None:  # noqa: D401
        """Dispatch to all registered callbacks."""
        with self._lock:
            callbacks = list(self._callbacks)
        for callback in callbacks:
            try:
                callback(msg)
            except Exception:  # pragma: no cover - defensive.
                LOGGER.exception("Listener callback raised")

    def register(self, callback: MessageCallback) -> None:
        with self._lock:
            if callback not in self._callbacks:
                self._callbacks.append(callback)

    def unregister(self, callback: MessageCallback) -> None:
        with self._lock:
            if callback in self._callbacks:
                self._callbacks.remove(callback)


class BusManager:
    """Manage a SocketCAN bus with shared Notifier and helper methods."""

    def __init__(
        self,
        channel: str,
        *,
        interface: str = "socketcan",
        bitrate: int | None = None,
        receive_own_messages: bool = False,
    ) -> None:
        if can is None:
            raise BusManagerError(
                "python-can is not available. Install the 'python-can' dependency."
            ) from _IMPORT_ERROR
        self._channel = channel
        self._interface = interface
        self._bitrate = bitrate
        self._receive_own_messages = receive_own_messages
        self._bus: Optional["can.BusABC"] = None
        self._notifier: Optional["can.Notifier"] = None
        self._callback_listener = _CallbackListener()
        self._reader = can.BufferedReader()

    @property
    def bus(self) -> "can.BusABC":
        if self._bus is None:
            raise BusManagerError("Bus is not open")
        return self._bus

    def open(self) -> None:
        if self._bus is not None:
            return
        LOGGER.info("Opening CAN bus: channel=%s interface=%s", self._channel, self._interface)
        try:
            self._bus = can.ThreadSafeBus(  # type: ignore[attr-defined]
                channel=self._channel,
                interface=self._interface,
                bitrate=self._bitrate,
                receive_own_messages=self._receive_own_messages,
            )
        except Exception as exc:  # pragma: no cover - hardware dependent
            self._bus = None
            raise BusManagerError(f"Failed to open bus '{self._channel}': {exc}") from exc
        self._notifier = can.Notifier(self._bus, [self._reader, self._callback_listener], 0.5)

    def close(self) -> None:
        if self._notifier is not None:
            self._notifier.stop()
            self._notifier = None
        if self._bus is not None:
            LOGGER.info("Closing CAN bus: %s", self._channel)
            self._bus.shutdown()
            self._bus = None

    def set_filters(self, filters: Iterable[dict[str, int]]) -> None:
        self.bus.set_filters(list(filters))

    def register_listener(self, callback: MessageCallback) -> None:
        """Register a callback to be notified for each incoming CAN message."""
        self._callback_listener.register(callback)

    def unregister_listener(self, callback: MessageCallback) -> None:
        self._callback_listener.unregister(callback)

    def get_message(self, timeout: float | None = None) -> "can.Message | None":
        """Retrieve the next buffered message from the reader."""
        return self._reader.get_message(timeout)

    def send(self, arbitration_id: int, data: bytes, *, extended_id: bool = False) -> None:
        message = can.Message(arbitration_id=arbitration_id, data=data, is_extended_id=extended_id)
        try:
            self.bus.send(message)
        except can.CanError as exc:  # pragma: no cover - hardware dependent
            raise BusManagerError(f"Failed to send message: {exc}") from exc

    def send_periodic(
        self,
        arbitration_id: int,
        data: bytes,
        *,
        hz: float,
        extended_id: bool = False,
        duration: float | None = None,
    ) -> PeriodicTask:
        if hz <= 0:
            raise ValueError("hz must be positive")
        period = 1.0 / hz
        message = can.Message(arbitration_id=arbitration_id, data=data, is_extended_id=extended_id)
        try:
            task = self.bus.send_periodic(message, period=period, duration=duration)
        except can.CanError as exc:  # pragma: no cover - hardware dependent
            raise BusManagerError(f"Failed to start periodic task: {exc}") from exc
        return PeriodicTask(task)

    def __enter__(self) -> "BusManager":
        self.open()
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        self.close()


__all__ = ["BusManager", "BusManagerError", "PeriodicTask"]
