"""Telemetry readers, selected by the install's tracing mode."""
from __future__ import annotations

from kge.adapters.tracing import mode
from kge.pipeline.settings import telemetry_monitoring_verified
from kge.adapters.telemetry.aggregate import Collection
from kge.ports import TelemetryReader, TelemetrySink


def reader(profile) -> TelemetryReader:
    """One client's telemetry reader; callers never select a backend."""
    selected = mode()
    if selected == "store":
        from kge.adapters.telemetry.store import for_profile
        return for_profile(profile)
    if selected == "cloudtrace":
        from kge.adapters.telemetry.cloud import Cloud
        return Cloud(profile.client, monitoring_verified=telemetry_monitoring_verified())
    return Collection([])


class SinkUnavailable(RuntimeError):
    """The selected tracing backend does not accept portal span ingestion."""


def sink(profile) -> TelemetrySink:
    """The client-scoped portal span sink selected with the tracing backend."""
    if mode() != "store":
        raise SinkUnavailable("the telemetry sink is not enabled")
    from kge.adapters.telemetry.store import for_profile
    return for_profile(profile)
