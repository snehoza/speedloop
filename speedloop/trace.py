"""Record spans in your own code and write them in the format ``speedloop ledger`` reads.

    from speedloop.trace import Tracer

    tracer = Tracer()
    with tracer.action("load_page"):
        with tracer.span("fetch"):
            ...
        with tracer.span("parse"):
            ...
    tracer.save("results-round1/trace.json")

Spans record the thread they ran on, so work started on a worker thread inside an action shows up as
"beside" work in the ledger. Spans opened outside any action are kept and fall into whichever action
window they overlap. A ``Tracer`` is thread-safe; one per process is enough.

``@tracer.timed("name")`` wraps a function in a span. Overhead is two ``perf_counter`` calls and a lock
per span, so keep spans at the level of real steps, not inner loops.
"""
from __future__ import annotations

import functools
import json
import threading
import time
from contextlib import contextmanager
from pathlib import Path


class Tracer:
    def __init__(self, clock=time.perf_counter):
        self._clock = clock
        self._lock = threading.Lock()
        self._spans: list[dict] = []
        self._actions: list[dict] = []

    @contextmanager
    def span(self, name: str):
        start = self._clock()
        try:
            yield
        finally:
            end = self._clock()
            with self._lock:
                self._spans.append({"name": name, "start": start, "end": end,
                                    "thread": threading.current_thread().name})

    @contextmanager
    def action(self, name: str):
        """One user-facing action: its window becomes one tree in the ledger."""
        start = self._clock()
        try:
            yield
        finally:
            end = self._clock()
            with self._lock:
                self._actions.append({"action": name, "start": start, "end": end})

    def timed(self, name: str | None = None):
        def wrap(fn):
            label = name or fn.__qualname__

            @functools.wraps(fn)
            def inner(*args, **kwargs):
                with self.span(label):
                    return fn(*args, **kwargs)
            return inner
        return wrap

    def runs(self) -> list[dict]:
        """One run per recorded action, with the spans that overlap its window."""
        with self._lock:
            spans, actions = list(self._spans), list(self._actions)
        return [{**a, "spans": [s for s in spans if s["end"] > a["start"] and s["start"] < a["end"]]} for a in actions]

    def save(self, path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({"runs": self.runs()}, indent=1), encoding="utf-8")
        return path
