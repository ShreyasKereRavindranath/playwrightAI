"""
Generic API client for REST calls made during test setup/teardown.

Use this for creating/deleting test data via API rather than through the UI.
Never use this inside Page Object methods.
"""

import logging
from typing import Any, Optional

import requests
from requests import Response, Session

from config.config import Config

logger = logging.getLogger(__name__)


class ApiClient:
    """Thin wrapper around requests.Session with base URL, auth, and logging."""

    def __init__(self, base_url: Optional[str] = None, token: Optional[str] = None) -> None:
        self.base_url = (base_url or Config.BASE_URL).rstrip("/")
        self.session = Session()
        if token:
            self.session.headers.update({"Authorization": f"Bearer {token}"})
        self.session.headers.update({"Content-Type": "application/json"})

    def get(self, path: str, **kwargs) -> Response:
        url = self._url(path)
        logger.debug("GET %s", url)
        response = self.session.get(url, **kwargs)
        self._log_response(response)
        return response

    def post(self, path: str, payload: Any = None, **kwargs) -> Response:
        url = self._url(path)
        logger.debug("POST %s — payload: %s", url, payload)
        response = self.session.post(url, json=payload, **kwargs)
        self._log_response(response)
        return response

    def put(self, path: str, payload: Any = None, **kwargs) -> Response:
        url = self._url(path)
        logger.debug("PUT %s — payload: %s", url, payload)
        response = self.session.put(url, json=payload, **kwargs)
        self._log_response(response)
        return response

    def delete(self, path: str, **kwargs) -> Response:
        url = self._url(path)
        logger.debug("DELETE %s", url)
        response = self.session.delete(url, **kwargs)
        self._log_response(response)
        return response

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _log_response(self, response: Response) -> None:
        level = logging.DEBUG if response.ok else logging.WARNING
        logger.log(level, "Response %s %s", response.status_code, response.url)
