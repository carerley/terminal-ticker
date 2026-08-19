from __future__ import annotations

import base64
import json
import struct
import threading
import time
from dataclasses import dataclass
from typing import Callable

from websockets.sync.client import connect


STREAM_URL = "wss://streamer.finance.yahoo.com/?version=2"


@dataclass(frozen=True)
class LiveQuote:
    symbol: str
    price: float
    change_percent: float | None
    market_hours: int | None
    timestamp: int


def _read_varint(data: bytes, offset: int) -> tuple[int, int]:
    value = 0
    shift = 0
    while offset < len(data):
        byte = data[offset]
        offset += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, offset
        shift += 7
        if shift >= 70:
            break
    raise ValueError("invalid protobuf varint")


def decode_live_quote(message: str) -> LiveQuote | None:
    """Decode the small subset of Yahoo's PricingData protobuf that we display."""
    try:
        envelope = json.loads(message)
        data = base64.b64decode(envelope["message"])
    except (KeyError, TypeError, ValueError, json.JSONDecodeError):
        return None

    values: dict[int, object] = {}
    offset = 0
    try:
        while offset < len(data):
            tag, offset = _read_varint(data, offset)
            field, wire_type = tag >> 3, tag & 7
            if wire_type == 0:
                value, offset = _read_varint(data, offset)
                if field in {3, 9, 14, 19, 21, 22, 24, 26, 27, 28, 29}:
                    value = (value >> 1) ^ -(value & 1)
            elif wire_type == 1:
                value = struct.unpack_from("<d", data, offset)[0]
                offset += 8
            elif wire_type == 2:
                length, offset = _read_varint(data, offset)
                value = data[offset : offset + length]
                offset += length
            elif wire_type == 5:
                value = struct.unpack_from("<f", data, offset)[0]
                offset += 4
            else:
                return None
            if field in {1, 2, 3, 7, 8}:
                values[field] = value
    except (IndexError, struct.error, ValueError):
        return None

    symbol_value, price_value = values.get(1), values.get(2)
    if not isinstance(symbol_value, bytes) or not isinstance(price_value, float):
        return None
    raw_time = int(values.get(3, time.time()))
    timestamp = raw_time // 1000 if raw_time > 10_000_000_000 else raw_time
    return LiveQuote(
        symbol=symbol_value.decode("utf-8", errors="replace").upper(),
        price=price_value,
        change_percent=float(values[8]) if isinstance(values.get(8), float) else None,
        market_hours=int(values[7]) if isinstance(values.get(7), int) else None,
        timestamp=timestamp,
    )


class QuoteStream:
    """Maintain one reconnecting Yahoo stream for a changing set of symbols."""

    def __init__(self, symbols: list[str], on_quote: Callable[[LiveQuote], None]):
        self._symbols = {symbol.upper() for symbol in symbols}
        self._on_quote = on_quote
        self._lock = threading.Lock()
        self._changed = threading.Event()
        self._stopped = threading.Event()
        self._thread = threading.Thread(target=self._run, name="ticker-stream", daemon=True)

    def start(self) -> None:
        self._thread.start()

    def subscribe(self, symbol: str) -> None:
        with self._lock:
            self._symbols.add(symbol.upper())
        self._changed.set()

    def unsubscribe(self, symbol: str) -> None:
        with self._lock:
            self._symbols.discard(symbol.upper())
        self._changed.set()

    def stop(self) -> None:
        self._stopped.set()
        self._changed.set()
        self._thread.join(timeout=2)

    def _snapshot(self) -> list[str]:
        with self._lock:
            return sorted(self._symbols)

    def _run(self) -> None:
        delay = 1
        while not self._stopped.is_set():
            symbols = self._snapshot()
            if not symbols:
                self._changed.wait(1)
                self._changed.clear()
                continue
            try:
                with connect(STREAM_URL, open_timeout=8, close_timeout=1) as websocket:
                    websocket.send(json.dumps({"subscribe": symbols}))
                    subscribed = set(symbols)
                    last_subscribe = time.monotonic()
                    delay = 1
                    while not self._stopped.is_set():
                        current = set(self._snapshot())
                        added, removed = current - subscribed, subscribed - current
                        if added:
                            websocket.send(json.dumps({"subscribe": sorted(added)}))
                        if removed:
                            websocket.send(json.dumps({"unsubscribe": sorted(removed)}))
                        subscribed = current
                        if current and time.monotonic() - last_subscribe >= 15:
                            websocket.send(json.dumps({"subscribe": sorted(current)}))
                            last_subscribe = time.monotonic()
                        self._changed.clear()
                        try:
                            message = websocket.recv(timeout=1)
                        except TimeoutError:
                            continue
                        if isinstance(message, str):
                            quote = decode_live_quote(message)
                            if quote and quote.symbol in current:
                                self._on_quote(quote)
            except Exception:
                # The unofficial stream must never take down the watchlist.
                pass
            if not self._stopped.wait(delay):
                delay = min(delay * 2, 30)
