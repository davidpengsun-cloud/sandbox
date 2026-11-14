"""Onboarding a business unit from the page: /onboard and the job it starts."""
from __future__ import annotations


class JobRefused(Exception):
    """A job that cannot start or be read, with the HTTP status the routes answer and a message safe to show."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status = status
        self.message = message


def load_job(app) -> dict | None:
    from kge.services.onboard.jobs import load as _load

    return _load(app)


def start_eval(*args, **kwargs) -> dict:
    from kge.services.onboard.jobs import start_eval as _start_eval

    return _start_eval(*args, **kwargs)


__all__ = ["JobRefused", "load_job", "start_eval"]
