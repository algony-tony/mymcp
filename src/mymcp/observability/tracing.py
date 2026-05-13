"""Tracer factory. Call sites use ``get_tracer(__name__)``."""

from __future__ import annotations

from opentelemetry import trace
from opentelemetry.trace import Tracer


def get_tracer(name: str) -> Tracer:
    return trace.get_tracer(name)
