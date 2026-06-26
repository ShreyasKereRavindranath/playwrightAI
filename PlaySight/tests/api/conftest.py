"""
API test fixtures — shared across all tests/api/ test files.

Target priority:
  1. API_BASE_URL env var  →  use local mock server
  2. default               →  public restful-booker.herokuapp.com

To run against the local mock server:
    # Terminal 1:
    python tools/mock_api_server.py
    # Terminal 2:
    API_BASE_URL=http://localhost:8765 pytest tests/api/ -v
  or permanently in config/.env:
    API_BASE_URL=http://localhost:8765
"""

import logging
import os

import pytest
import requests

from config.config import Config

logger = logging.getLogger(__name__)

API_BASE_URL = os.getenv("API_BASE_URL", "https://restful-booker.herokuapp.com")


@pytest.fixture(scope="session")
def api_session() -> requests.Session:
    """A requests.Session pre-configured with common headers."""
    session = requests.Session()
    session.headers.update({
        "Content-Type": "application/json",
        "Accept": "application/json",
    })
    api_key = getattr(Config, "API_KEY", "")
    if api_key:
        session.headers["Authorization"] = f"Bearer {api_key}"
    yield session
    session.close()


@pytest.fixture(scope="session")
def auth_token(api_session: requests.Session) -> str:
    """Create an auth token from the API (reused for the whole session)."""
    response = api_session.post(
        f"{API_BASE_URL}/auth",
        json={"username": "admin", "password": "password123"},
        timeout=15,
    )
    assert response.status_code == 200, f"Auth failed: {response.text}"
    token = response.json().get("token", "")
    assert token, "Auth response missing token"
    logger.info("API auth token obtained")
    return token


@pytest.fixture
def new_booking_payload() -> dict:
    """Valid booking payload for POST /booking contract tests."""
    return {
        "firstname": "Jane",
        "lastname":  "QA",
        "totalprice": 200,
        "depositpaid": True,
        "bookingdates": {
            "checkin":  "2026-07-01",
            "checkout": "2026-07-07",
        },
        "additionalneeds": "Breakfast",
    }
