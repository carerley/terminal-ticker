from __future__ import annotations

import json
import sys
import time
from contextlib import contextmanager
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterator, TextIO


@dataclass
class Event:
    name: str
    duration_ms: float
    metadata: dict[str, Any] = field(default_factory=dict)
    started_ms: float = 0.0


class Profiler:
    """Low-overhead, opt-in wall-clock profiler for CLI operations."""

    def __init__(self, enabled: bool = False, started_at: float | None = None):
        self.enabled = enabled
        self.started_at = started_at if started_at is not None else time.perf_counter()
        self.events: list[Event] = []

    @contextmanager
    def span(self, name: str, **metadata: Any) -> Iterator[dict[str, Any]]:
        if not self.enabled:
            yield metadata
            return
        started = time.perf_counter()
        try:
            yield metadata
        finally:
            self.events.append(
                Event(
                    name,
                    (time.perf_counter() - started) * 1000,
                    metadata,
                    (started - self.started_at) * 1000,
                )
            )

    def elapsed_ms(self) -> float:
        return (time.perf_counter() - self.started_at) * 1000

    def write_report(self, stream: TextIO | None = None) -> None:
        stream = stream or sys.stderr
        total = self.elapsed_ms()
        network_events = [event for event in self.events if event.name == "network"]
        network = _wall_time(network_events)
        aggregate_network = sum(event.duration_ms for event in network_events)
        print("\nticker profile", file=stream)
        print("─" * 52, file=stream)
        for event in self.events:
            details = " ".join(
                f"{key}={value}" for key, value in event.metadata.items() if value is not None
            )
            label = f"{event.name} ({details})" if details else event.name
            print(f"{label[:40]:<40} {event.duration_ms:>9.1f} ms", file=stream)
        print("─" * 52, file=stream)
        print(f"{'Total':<40} {total:>9.1f} ms", file=stream)
        if total:
            print(
                f"Network activity: {network:.1f} ms wall ({network / total * 100:.0f}%), "
                f"{aggregate_network:.1f} ms aggregate",
                file=stream,
            )
        requests = [event for event in self.events if event.name == "request"]
        if requests:
            slowest = max(requests, key=lambda event: event.duration_ms)
            symbol = slowest.metadata.get("symbol", "?")
            period = slowest.metadata.get("range", "?")
            print(
                f"Slowest request: {symbol} range={period}, {slowest.duration_ms:.1f} ms",
                file=stream,
            )
            peak = _peak_concurrency(requests)
            print(f"Peak request concurrency: {peak}", file=stream)
        if network > total * 0.8:
            print("Diagnosis: network-bound", file=stream)
        if len(requests) > 1 and _peak_concurrency(requests) == 1:
            print(f"Diagnosis: {len(requests)} requests executed sequentially", file=stream)

    def write_json(self, path: str | Path) -> None:
        value = {
            "total_ms": round(self.elapsed_ms(), 3),
            "events": [asdict(event) for event in self.events],
        }
        Path(path).write_text(json.dumps(value, indent=2) + "\n", encoding="utf-8")


def _peak_concurrency(events: list[Event]) -> int:
    boundaries: list[tuple[float, int]] = []
    for event in events:
        boundaries.append((event.started_ms, 1))
        boundaries.append((event.started_ms + event.duration_ms, -1))
    active = peak = 0
    for _, change in sorted(boundaries, key=lambda item: (item[0], item[1])):
        active += change
        peak = max(peak, active)
    return peak


def _wall_time(events: list[Event]) -> float:
    intervals = sorted(
        (event.started_ms, event.started_ms + event.duration_ms) for event in events
    )
    if not intervals:
        return 0.0
    total = 0.0
    start, end = intervals[0]
    for next_start, next_end in intervals[1:]:
        if next_start > end:
            total += end - start
            start, end = next_start, next_end
        else:
            end = max(end, next_end)
    return total + end - start
