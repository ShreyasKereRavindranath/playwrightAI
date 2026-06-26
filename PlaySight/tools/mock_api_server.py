#!/usr/bin/env python3
"""
PlaySight Mock API Server — Capability #8 (local target)

Mimics the restful-booker REST API so API contract tests run fully offline,
with zero dependency on an external service.

Endpoints (identical contract to restful-booker.herokuapp.com):
  POST   /auth              → {"token": "..."}  or {"reason": "Bad credentials"}
  GET    /booking           → [{"bookingid": 1}, ...]
  GET    /booking/{id}      → Booking object or 404
  POST   /booking           → {"bookingid": n, "booking": {...}}
  PUT    /booking/{id}      → Updated booking  (requires Cookie: token=...)
  PATCH  /booking/{id}      → Partial update   (requires Cookie: token=...)
  DELETE /booking/{id}      → 201 "Created"    (requires Cookie: token=...)
  GET    /ping              → "pong"

Usage:
    python tools/mock_api_server.py             # default port 8765
    python tools/mock_api_server.py --port 9000

Then point tests at it:
    API_BASE_URL=http://localhost:8765 pytest tests/api/ -v
  or set in config/.env:
    API_BASE_URL=http://localhost:8765
"""

import argparse
import secrets
import sys
import threading
from typing import Any, Dict, Optional

try:
    import uvicorn
    from fastapi import Cookie, FastAPI, HTTPException, Query, Request
    from fastapi.responses import JSONResponse, PlainTextResponse
    from pydantic import BaseModel
except ImportError:
    print("Missing dependencies. Run: pip install fastapi uvicorn[standard]")
    sys.exit(1)

app = FastAPI(title="PlaySight Mock API", version="1.0.0")

# ── In-memory store ────────────────────────────────────────────────────────
_lock     = threading.Lock()
_store: Dict[int, dict] = {}
_counter  = 1
_tokens: set = set()

_VALID_CREDS = {"admin": "password123"}


def _next_id() -> int:
    global _counter
    with _lock:
        _counter += 1
        return _counter - 1


# ── Pydantic models ────────────────────────────────────────────────────────

class BookingDates(BaseModel):
    checkin:  str
    checkout: str


class BookingIn(BaseModel):
    firstname:        str
    lastname:         str
    totalprice:       int
    depositpaid:      bool
    bookingdates:     BookingDates
    additionalneeds:  Optional[str] = None


class BookingPatch(BaseModel):
    firstname:       Optional[str]   = None
    lastname:        Optional[str]   = None
    totalprice:      Optional[int]   = None
    depositpaid:     Optional[bool]  = None
    bookingdates:    Optional[BookingDates] = None
    additionalneeds: Optional[str]   = None


class AuthRequest(BaseModel):
    username: str
    password: str


# ── Auth middleware helper ─────────────────────────────────────────────────

def _require_auth(token: Optional[str]) -> None:
    if not token or token not in _tokens:
        raise HTTPException(status_code=403, detail="Forbidden")


# ── Seed some default bookings on startup ─────────────────────────────────

def _seed():
    defaults = [
        {"firstname": "Alice", "lastname": "Smith",   "totalprice": 150, "depositpaid": True,
         "bookingdates": {"checkin": "2026-07-01", "checkout": "2026-07-05"}, "additionalneeds": "Breakfast"},
        {"firstname": "Bob",   "lastname": "Jones",   "totalprice": 300, "depositpaid": False,
         "bookingdates": {"checkin": "2026-08-10", "checkout": "2026-08-15"}, "additionalneeds": ""},
        {"firstname": "Carol", "lastname": "Williams","totalprice": 210, "depositpaid": True,
         "bookingdates": {"checkin": "2026-09-01", "checkout": "2026-09-03"}, "additionalneeds": "Late checkout"},
    ]
    for d in defaults:
        bid = _next_id()
        _store[bid] = d


_seed()


# ── Endpoints ──────────────────────────────────────────────────────────────

@app.get("/ping", response_class=PlainTextResponse)
def ping():
    return "pong"


@app.post("/auth")
def create_token(body: AuthRequest):
    if _VALID_CREDS.get(body.username) == body.password:
        token = secrets.token_hex(8)
        _tokens.add(token)
        return {"token": token}
    return JSONResponse({"reason": "Bad credentials"}, status_code=200)


@app.get("/booking")
def list_bookings(
    firstname: Optional[str] = Query(None),
    lastname:  Optional[str] = Query(None),
):
    with _lock:
        items = list(_store.items())
    result = []
    for bid, b in items:
        if firstname and b["firstname"].lower() != firstname.lower():
            continue
        if lastname and b["lastname"].lower() != lastname.lower():
            continue
        result.append({"bookingid": bid})
    return result


@app.get("/booking/{booking_id}")
def get_booking(booking_id: int):
    with _lock:
        b = _store.get(booking_id)
    if b is None:
        raise HTTPException(status_code=404, detail="Not Found")
    return b


@app.post("/booking", status_code=200)
def create_booking(body: BookingIn):
    bid = _next_id()
    data = body.model_dump()
    with _lock:
        _store[bid] = data
    return {"bookingid": bid, "booking": data}


@app.put("/booking/{booking_id}")
def update_booking(booking_id: int, body: BookingIn, token: Optional[str] = Cookie(None)):
    _require_auth(token)
    with _lock:
        if booking_id not in _store:
            raise HTTPException(status_code=404, detail="Not Found")
        _store[booking_id] = body.model_dump()
    return _store[booking_id]


@app.patch("/booking/{booking_id}")
def partial_update(booking_id: int, body: BookingPatch, token: Optional[str] = Cookie(None)):
    _require_auth(token)
    with _lock:
        if booking_id not in _store:
            raise HTTPException(status_code=404, detail="Not Found")
        existing = dict(_store[booking_id])
        patch = body.model_dump(exclude_none=True)
        if "bookingdates" in patch:
            existing["bookingdates"].update(patch.pop("bookingdates"))
        existing.update(patch)
        _store[booking_id] = existing
    return _store[booking_id]


@app.delete("/booking/{booking_id}", status_code=201, response_class=PlainTextResponse)
def delete_booking(booking_id: int, token: Optional[str] = Cookie(None)):
    _require_auth(token)
    with _lock:
        if booking_id not in _store:
            raise HTTPException(status_code=404, detail="Not Found")
        del _store[booking_id]
    return "Created"


# ── Entry point ────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="PlaySight Mock API Server")
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--host", default="127.0.0.1")
    args = parser.parse_args()
    print(f"\n🚀 PlaySight Mock API running at http://{args.host}:{args.port}")
    print(f"   Docs: http://{args.host}:{args.port}/docs")
    print(f"   Set API_BASE_URL=http://{args.host}:{args.port} in config/.env\n")
    uvicorn.run(app, host=args.host, port=args.port, log_level="warning")


if __name__ == "__main__":
    main()
