"""Adapter from native terminal byte streams to CAO's event-bus output topic.

POSIX tmux uses ``fifo_reader`` for this job. A ConPTY backend already owns the
terminal output stream, so this manager forwards it directly without creating a
FIFO. Subscribers receive the same ``{"data": text}`` payload as the FIFO path.
"""

from __future__ import annotations

import codecs
import logging
import queue
import threading
import time
from dataclasses import dataclass
from typing import Any

from cli_agent_orchestrator.services.event_bus import bus

logger = logging.getLogger(__name__)
_POLL_SECONDS = 0.5
_COALESCE_SECONDS = 0.05
_MAX_BATCH_BYTES = 64 * 1024


@dataclass
class _Stream:
    backend: Any
    subscription: Any
    stop: threading.Event
    thread: threading.Thread


class OutputStreamManager:
    """Own one event-forwarding reader per backend-managed terminal."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._streams: dict[str, _Stream] = {}

    def start(self, terminal_id: str, backend: Any) -> None:
        with self._lock:
            if terminal_id in self._streams:
                raise RuntimeError(f"Output stream already started for {terminal_id}")
            subscription = backend.subscribe_output(terminal_id)
            stop = threading.Event()
            thread = threading.Thread(
                target=self._pump,
                args=(terminal_id, subscription, stop),
                name=f"cao-output-{terminal_id}",
                daemon=True,
            )
            stream = _Stream(backend, subscription, stop, thread)
            self._streams[terminal_id] = stream
            thread.start()

    @staticmethod
    def _pump(terminal_id: str, subscription: Any, stop: threading.Event) -> None:
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
        pending = bytearray()
        last_publish = time.monotonic()
        topic = f"terminal.{terminal_id}.output"

        def publish(final: bool = False) -> None:
            nonlocal last_publish
            text = decoder.decode(bytes(pending), final=final)
            pending.clear()
            if text:
                bus.publish(topic, {"data": text})
            last_publish = time.monotonic()

        while not stop.is_set():
            try:
                chunk = subscription.get(timeout=_POLL_SECONDS)
                if isinstance(chunk, bytes):
                    if chunk == b"":
                        break
                    pending.extend(chunk)
            except queue.Empty:
                pass
            except Exception:
                logger.exception("Native output stream failed for terminal %s", terminal_id)
                break

            now = time.monotonic()
            if pending and (now - last_publish >= _COALESCE_SECONDS or len(pending) >= _MAX_BATCH_BYTES):
                publish()

        if pending:
            publish(final=True)
        else:
            tail = decoder.decode(b"", final=True)
            if tail:
                bus.publish(topic, {"data": tail})

    def stop(self, terminal_id: str, *, timeout: float = 2.0) -> bool:
        with self._lock:
            stream = self._streams.get(terminal_id)
            if stream is None:
                return True
            stream.stop.set()
        stream.thread.join(timeout=timeout)
        if stream.thread.is_alive():
            logger.warning("Native output stream for %s did not stop within %.1fs", terminal_id, timeout)
            return False
        try:
            stream.backend.unsubscribe_output(terminal_id, stream.subscription)
        except Exception:
            logger.exception("Failed to unsubscribe native output for %s", terminal_id)
            return False
        with self._lock:
            if self._streams.get(terminal_id) is stream:
                self._streams.pop(terminal_id, None)
        return True

    def stop_all(self, *, timeout: float = 2.0) -> bool:
        with self._lock:
            terminal_ids = list(self._streams)
        complete = True
        for terminal_id in terminal_ids:
            complete = self.stop(terminal_id, timeout=timeout) and complete
        return complete


output_stream_manager = OutputStreamManager()
