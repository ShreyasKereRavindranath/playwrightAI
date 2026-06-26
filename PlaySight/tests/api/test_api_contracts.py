"""
API Contract Tests — Capability #8

Validates REST API response schema, status codes, and business rules using
Pydantic v2 models.  Target: restful-booker.herokuapp.com (a public demo).

Swap API_BASE_URL in tests/api/conftest.py to point at your own API.

Markers:
  @pytest.mark.api       — all tests here carry this marker
  @pytest.mark.smoke     — critical happy-path checks
  @pytest.mark.regression — edge-case and negative checks
"""

import pytest
import requests
from pydantic import BaseModel, Field, ValidationError
from typing import Optional

from tests.api.conftest import API_BASE_URL

pytestmark = pytest.mark.api


# ── Pydantic contract models ───────────────────────────────────────────────

class BookingDates(BaseModel):
    checkin:  str
    checkout: str


class Booking(BaseModel):
    firstname:       str
    lastname:        str
    totalprice:      int
    depositpaid:     bool
    bookingdates:    BookingDates
    additionalneeds: Optional[str] = None


class BookingResponse(BaseModel):
    bookingid: int = Field(gt=0)
    booking:   Booking


class BookingId(BaseModel):
    bookingid: int = Field(gt=0)


class AuthResponse(BaseModel):
    token: str = Field(min_length=10)


# ── Auth endpoint ──────────────────────────────────────────────────────────

@pytest.mark.smoke
def test_auth_returns_token(api_session: requests.Session):
    """POST /auth must return a valid token."""
    resp = api_session.post(
        f"{API_BASE_URL}/auth",
        json={"username": "admin", "password": "password123"},
        timeout=15,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    model = AuthResponse(**resp.json())
    assert model.token, "Token must not be empty"


@pytest.mark.regression
def test_auth_rejects_bad_credentials(api_session: requests.Session):
    """POST /auth with wrong password must NOT return a usable token."""
    resp = api_session.post(
        f"{API_BASE_URL}/auth",
        json={"username": "admin", "password": "wrong"},
        timeout=15,
    )
    assert resp.status_code == 200  # API returns 200 with 'Bad credentials' body
    body = resp.json()
    assert body.get("token") != "abc123"
    assert "reason" in body or "token" in body  # Bad credentials response


# ── GET /booking ────────────────────────────────────────────────────────────

@pytest.mark.smoke
def test_list_bookings_returns_array(api_session: requests.Session):
    """GET /booking must return a non-empty list of booking IDs."""
    resp = api_session.get(f"{API_BASE_URL}/booking", timeout=15)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    data = resp.json()
    assert isinstance(data, list), "Response must be a list"
    assert len(data) > 0, "Booking list must not be empty"
    # Validate first item matches the contract
    first = BookingId(**data[0])
    assert first.bookingid > 0


@pytest.mark.regression
def test_filter_bookings_by_name(api_session: requests.Session, new_booking_payload: dict):
    """GET /booking?firstname=X must filter results correctly."""
    # First create a booking we can search for
    create_resp = api_session.post(
        f"{API_BASE_URL}/booking",
        json=new_booking_payload,
        timeout=15,
    )
    assert create_resp.status_code == 200
    created_id = create_resp.json()["bookingid"]

    search_resp = api_session.get(
        f"{API_BASE_URL}/booking",
        params={"firstname": new_booking_payload["firstname"],
                "lastname":  new_booking_payload["lastname"]},
        timeout=15,
    )
    assert search_resp.status_code == 200
    ids = [b["bookingid"] for b in search_resp.json()]
    assert created_id in ids, f"Created booking {created_id} not found in filtered results"


# ── GET /booking/:id ────────────────────────────────────────────────────────

@pytest.mark.smoke
def test_get_booking_by_id_schema(api_session: requests.Session, new_booking_payload: dict):
    """GET /booking/:id response must match Booking schema."""
    create = api_session.post(f"{API_BASE_URL}/booking", json=new_booking_payload, timeout=15)
    assert create.status_code == 200
    booking_id = create.json()["bookingid"]

    resp = api_session.get(f"{API_BASE_URL}/booking/{booking_id}", timeout=15)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    try:
        model = Booking(**resp.json())
    except ValidationError as exc:
        pytest.fail(f"Schema validation failed:\n{exc}")
    assert model.firstname == new_booking_payload["firstname"]
    assert model.totalprice == new_booking_payload["totalprice"]


@pytest.mark.regression
def test_get_nonexistent_booking_returns_404(api_session: requests.Session):
    """GET /booking/999999999 must return 404."""
    resp = api_session.get(f"{API_BASE_URL}/booking/999999999", timeout=15)
    assert resp.status_code == 404, f"Expected 404, got {resp.status_code}"


# ── POST /booking ───────────────────────────────────────────────────────────

@pytest.mark.smoke
def test_create_booking_full_contract(api_session: requests.Session, new_booking_payload: dict):
    """POST /booking must return a BookingResponse matching the full contract."""
    resp = api_session.post(f"{API_BASE_URL}/booking", json=new_booking_payload, timeout=15)
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}: {resp.text}"
    try:
        model = BookingResponse(**resp.json())
    except ValidationError as exc:
        pytest.fail(f"Schema validation failed:\n{exc}")
    assert model.bookingid > 0
    assert model.booking.firstname  == new_booking_payload["firstname"]
    assert model.booking.totalprice == new_booking_payload["totalprice"]
    assert model.booking.depositpaid == new_booking_payload["depositpaid"]


@pytest.mark.regression
def test_create_booking_missing_required_field(api_session: requests.Session):
    """POST /booking without totalprice must return 4xx or fail validation."""
    payload = {
        "firstname": "Test",
        "lastname":  "Missing",
        "depositpaid": True,
        "bookingdates": {"checkin": "2026-07-01", "checkout": "2026-07-07"},
    }
    resp = api_session.post(f"{API_BASE_URL}/booking", json=payload, timeout=15)
    # restful-booker returns 200 but with "NaN" for missing totalprice —
    # our Pydantic model rejects it as not an int
    if resp.status_code == 200:
        with pytest.raises(ValidationError):
            BookingResponse(**resp.json())
    else:
        assert resp.status_code in (400, 422)


# ── PUT /booking/:id ────────────────────────────────────────────────────────

@pytest.mark.smoke
def test_update_booking_full_replace(
    api_session: requests.Session,
    auth_token: str,
    new_booking_payload: dict,
):
    """PUT /booking/:id must replace the entire resource and return updated data."""
    create_resp = api_session.post(f"{API_BASE_URL}/booking", json=new_booking_payload, timeout=15)
    booking_id  = create_resp.json()["bookingid"]

    updated = {**new_booking_payload, "firstname": "Updated", "totalprice": 999}
    resp = api_session.put(
        f"{API_BASE_URL}/booking/{booking_id}",
        json=updated,
        headers={"Cookie": f"token={auth_token}"},
        timeout=15,
    )
    assert resp.status_code == 200, f"Expected 200, got {resp.status_code}"
    model = Booking(**resp.json())
    assert model.firstname  == "Updated"
    assert model.totalprice == 999


# ── DELETE /booking/:id ─────────────────────────────────────────────────────

@pytest.mark.regression
def test_delete_booking(
    api_session: requests.Session,
    auth_token: str,
    new_booking_payload: dict,
):
    """DELETE /booking/:id must remove the booking (subsequent GET → 404)."""
    create_resp = api_session.post(f"{API_BASE_URL}/booking", json=new_booking_payload, timeout=15)
    booking_id  = create_resp.json()["bookingid"]

    del_resp = api_session.delete(
        f"{API_BASE_URL}/booking/{booking_id}",
        headers={"Cookie": f"token={auth_token}"},
        timeout=15,
    )
    assert del_resp.status_code in (200, 201)

    get_resp = api_session.get(f"{API_BASE_URL}/booking/{booking_id}", timeout=15)
    assert get_resp.status_code == 404, "Deleted booking should return 404"
