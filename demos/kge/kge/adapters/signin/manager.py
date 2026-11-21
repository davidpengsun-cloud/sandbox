"""Read the current password secret with the service's existing ADC accessor."""
from __future__ import annotations

import base64
import json
import re
import urllib.error
import urllib.request

from kge.adapters import http
from kge.adapters.bigquery import bq_account
from kge.adapters.deadline import ExternalCallError


class SecretManagerReader:
    def __init__(self, *, endpoint="https://secretmanager.googleapis.com/v1", token=None):
        self.endpoint = endpoint.rstrip("/")
        self.token = token or self._token
        self._account = None

    def _token(self) -> str:
        import google.auth.exceptions
        try:
            if self._account is None:
                self._account = bq_account()
            credentials, _ = self._account
            if not credentials.valid:
                from google.auth.transport.requests import Request
                credentials.refresh(Request())
            return credentials.token
        except (google.auth.exceptions.GoogleAuthError, ValueError):
            raise ExternalCallError("Sign-in passwords unavailable: Secret Manager authentication failed") from None

    def latest(self, resource: str) -> bytes:
        if not re.fullmatch(r"projects/[A-Za-z0-9._:-]+/secrets/[A-Za-z0-9_-]+", resource):
            raise ExternalCallError("Sign-in passwords unavailable: invalid passwords-secret pointer")
        request = urllib.request.Request(f"{self.endpoint}/{resource}/versions/latest:access",
                                         headers={"Authorization": f"Bearer {self.token()}"})
        try:
            with http.open_http(request, timeout=15) as response:
                data = json.loads(response.read())
            return base64.b64decode(data["payload"]["data"], validate=True)
        except urllib.error.HTTPError as exc:
            raise ExternalCallError(f"Sign-in passwords unavailable: Secret Manager HTTP {exc.code}") from None
        except (OSError, ValueError, KeyError, TypeError):
            raise ExternalCallError("Sign-in passwords unavailable: Secret Manager read failed") from None
