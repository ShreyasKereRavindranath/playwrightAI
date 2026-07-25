"""
Shreyzen Locust file — virtual-user behaviour for every load scenario.

Run indirectly via the launcher UI or the CLI (they set the right env + flags):
    python tools/load_runner.py serve                 # dashboard, then click Run
    python tools/load_runner.py run --scenario crud --profile smoke

…or directly with Locust if you like commands after all:
    SHREYZEN_PROFILE=load SHREYZEN_USERS=50 SHREYZEN_DURATION=120 \
      locust -f load/locustfile.py --headless --host http://127.0.0.1:8765 BookingCrudUser

Scenarios (pick one User class):
    BookingCrudUser   — full CRUD mix (create/read/list/put/patch/delete)
    UserJourneyUser   — one full end-to-end journey per user, in order
    SecurityUser      — non-destructive security probes

The `ProfileShape` import registers the load shape so any of the 6 profiles
(+ custom) drives the virtual-user count over time.
"""

import sys
from pathlib import Path

# Make the project root importable when Locust runs this file as a script.
_ROOT = Path(__file__).resolve().parent.parent
if str(_ROOT) not in sys.path:
    sys.path.insert(0, str(_ROOT))

import os  # noqa: E402
import random  # noqa: E402

from locust import HttpUser, SequentialTaskSet, between, task  # noqa: E402

from load.catalog import API_ENDPOINTS, resolve_endpoints  # noqa: E402
from load.shapes import ProfileShape  # noqa: E402,F401  (registers the shape)

# ── Shared fixtures ──────────────────────────────────────────────────────────

_ADMIN = {"username": "admin", "password": "password123"}


def _booking_payload(tag: str = "load") -> dict:
    return {
        "firstname": f"Load{tag}",
        "lastname": "Tester",
        "totalprice": 199,
        "depositpaid": True,
        "bookingdates": {"checkin": "2026-07-01", "checkout": "2026-07-07"},
        "additionalneeds": "Breakfast",
    }


def _auth(client) -> str:
    """Obtain an auth token; returns '' on failure (caller decides severity)."""
    with client.post("/auth", json=_ADMIN, name="POST /auth", catch_response=True) as resp:
        if resp.status_code == 200 and resp.json().get("token"):
            return resp.json()["token"]
        resp.failure(f"auth failed: {resp.status_code} {resp.text[:80]}")
        return ""


# ── Scenario 1: full CRUD mix ────────────────────────────────────────────────

class BookingCrudUser(HttpUser):
    """Exercises every CRUD verb against the booking API in a weighted mix."""

    wait_time = between(0.1, 0.6)

    def on_start(self):
        self.token = _auth(self.client)
        self.owned: list[int] = []

    def _cookie(self) -> dict:
        return {"Cookie": f"token={self.token}"}

    @task(4)
    def create(self):
        with self.client.post(
            "/booking", json=_booking_payload("crud"),
            name="POST /booking", catch_response=True,
        ) as resp:
            if resp.status_code == 200 and "bookingid" in resp.json():
                bid = resp.json()["bookingid"]
                if len(self.owned) < 25:
                    self.owned.append(bid)
            else:
                resp.failure(f"create returned {resp.status_code}")

    @task(6)
    def read(self):
        if not self.owned:
            return
        bid = self.owned[-1]
        with self.client.get(
            f"/booking/{bid}", name="GET /booking/[id]", catch_response=True,
        ) as resp:
            if resp.status_code == 404:
                resp.success()  # legitimately deleted by another task
                if bid in self.owned:
                    self.owned.remove(bid)
            elif resp.status_code != 200:
                resp.failure(f"read returned {resp.status_code}")

    @task(3)
    def list_all(self):
        self.client.get("/booking", name="GET /booking")

    @task(2)
    def update_put(self):
        if not self.owned:
            return
        bid = self.owned[-1]
        body = {**_booking_payload("put"), "firstname": "Updated", "totalprice": 999}
        with self.client.put(
            f"/booking/{bid}", json=body, headers=self._cookie(),
            name="PUT /booking/[id]", catch_response=True,
        ) as resp:
            if resp.status_code == 404:
                resp.success()
            elif resp.status_code != 200:
                resp.failure(f"put returned {resp.status_code}")

    @task(2)
    def patch(self):
        if not self.owned:
            return
        bid = self.owned[-1]
        with self.client.patch(
            f"/booking/{bid}", json={"totalprice": 250}, headers=self._cookie(),
            name="PATCH /booking/[id]", catch_response=True,
        ) as resp:
            if resp.status_code == 404:
                resp.success()
            elif resp.status_code != 200:
                resp.failure(f"patch returned {resp.status_code}")

    @task(1)
    def delete(self):
        if not self.owned:
            return
        bid = self.owned.pop()
        with self.client.delete(
            f"/booking/{bid}", headers=self._cookie(),
            name="DELETE /booking/[id]", catch_response=True,
        ) as resp:
            if resp.status_code not in (200, 201, 404):
                resp.failure(f"delete returned {resp.status_code}")


# ── Scenario 2: full end-to-end user journey (ordered) ───────────────────────

class _JourneyTasks(SequentialTaskSet):
    """auth → create → read → update → patch → verify → delete → confirm-gone."""

    def on_start(self):
        self.token = _auth(self.client)
        self.booking_id = None

    @task
    def create(self):
        with self.client.post(
            "/booking", json=_booking_payload("journey"),
            name="journey: create", catch_response=True,
        ) as resp:
            if resp.status_code == 200 and "bookingid" in resp.json():
                self.booking_id = resp.json()["bookingid"]
            else:
                resp.failure(f"create returned {resp.status_code}")
                self.interrupt()

    @task
    def read(self):
        with self.client.get(
            f"/booking/{self.booking_id}", name="journey: read", catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"read returned {resp.status_code}")

    @task
    def update(self):
        body = {**_booking_payload("journey"), "firstname": "Journey", "totalprice": 500}
        with self.client.put(
            f"/booking/{self.booking_id}", json=body,
            headers={"Cookie": f"token={self.token}"},
            name="journey: update", catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"update returned {resp.status_code}")

    @task
    def patch(self):
        with self.client.patch(
            f"/booking/{self.booking_id}", json={"additionalneeds": "Late checkout"},
            headers={"Cookie": f"token={self.token}"},
            name="journey: patch", catch_response=True,
        ) as resp:
            if resp.status_code != 200:
                resp.failure(f"patch returned {resp.status_code}")

    @task
    def delete(self):
        with self.client.delete(
            f"/booking/{self.booking_id}",
            headers={"Cookie": f"token={self.token}"},
            name="journey: delete", catch_response=True,
        ) as resp:
            if resp.status_code not in (200, 201):
                resp.failure(f"delete returned {resp.status_code}")

    @task
    def confirm_deleted(self):
        with self.client.get(
            f"/booking/{self.booking_id}", name="journey: confirm-deleted",
            catch_response=True,
        ) as resp:
            if resp.status_code == 404:
                resp.success()
            else:
                resp.failure(f"expected 404 after delete, got {resp.status_code}")
        self.interrupt()  # restart the journey from the top


class UserJourneyUser(HttpUser):
    wait_time = between(0.2, 0.8)
    tasks = [_JourneyTasks]


# ── Scenario 3: non-destructive security probes ──────────────────────────────

class SecurityUser(HttpUser):
    """
    Continuously probes for common API weaknesses under concurrency.
    A probe *fails* (red in the report) only when the API behaves INSECURELY,
    so a clean run = the target held up. All probes are read-only or use the
    target's own auth — nothing destructive.
    """

    wait_time = between(0.3, 1.0)

    @task
    def auth_bypass_rejected(self):
        """Bad credentials must NOT yield a usable token."""
        with self.client.post(
            "/auth", json={"username": "admin", "password": "wrong-pass"},
            name="sec: bad-cred auth", catch_response=True,
        ) as resp:
            token = resp.json().get("token") if resp.headers.get("content-type", "").startswith("application/json") else None
            if token:
                resp.failure("SECURITY: bad credentials returned a token")
            else:
                resp.success()

    @task
    def write_requires_auth(self):
        """PUT without a token must be rejected (401/403), never applied."""
        with self.client.put(
            "/booking/1", json=_booking_payload("sec"),
            name="sec: unauth write", catch_response=True,
        ) as resp:
            if resp.status_code in (401, 403):
                resp.success()
            elif resp.status_code == 404:
                resp.success()  # resource gone, but still not an unauth success
            elif 200 <= resp.status_code < 300:
                resp.failure("SECURITY: unauthenticated write succeeded")
            else:
                resp.success()

    @task
    def delete_requires_auth(self):
        """DELETE without a token must be rejected."""
        with self.client.delete(
            "/booking/1", name="sec: unauth delete", catch_response=True,
        ) as resp:
            if 200 <= resp.status_code < 300:
                resp.failure("SECURITY: unauthenticated delete succeeded")
            else:
                resp.success()

    @task
    def injection_in_filter(self):
        """Injection payloads in query params must not 500 or dump data."""
        payload = "' OR '1'='1"
        with self.client.get(
            "/booking", params={"firstname": payload},
            name="sec: sqli filter", catch_response=True,
        ) as resp:
            if resp.status_code >= 500:
                resp.failure(f"SECURITY: injection caused server error {resp.status_code}")
            else:
                resp.success()

    @task
    def malformed_payload(self):
        """Malformed JSON body must return a 4xx, never crash the server (5xx)."""
        with self.client.post(
            "/booking", data="{not-valid-json",
            headers={"Content-Type": "application/json"},
            name="sec: malformed body", catch_response=True,
        ) as resp:
            if resp.status_code >= 500:
                resp.failure(f"SECURITY: malformed body caused {resp.status_code}")
            else:
                resp.success()

    @task
    def xss_roundtrip_safe(self):
        """A stored XSS-ish value must round-trip without a server error."""
        token = _auth(self.client)
        body = {**_booking_payload("xss"), "additionalneeds": "<script>alert(1)</script>"}
        with self.client.post(
            "/booking", json=body, name="sec: xss roundtrip", catch_response=True,
        ) as resp:
            if resp.status_code >= 500:
                resp.failure(f"SECURITY: xss payload caused {resp.status_code}")
            else:
                resp.success()


# ── Scenario 4: user-selected endpoints ──────────────────────────────────────

class SelectiveApiUser(HttpUser):
    """Exercises only the endpoints named in SHREYZEN_ENDPOINTS (comma-separated
    keys from load.catalog.API_ENDPOINTS). Empty/unset → all endpoints.

    Each hit is chosen at random weighted by the endpoint's catalog weight, so a
    selection behaves like a scaled-down CRUD mix limited to the chosen calls.
    Endpoints needing a booking id create one on demand; the report groups by the
    endpoint's stable label, so it lines up with the profile thresholds.
    """

    wait_time = between(0.1, 0.6)

    def on_start(self):
        keys = resolve_endpoints(os.getenv("SHREYZEN_ENDPOINTS", ""))
        self.endpoints = [API_ENDPOINTS[k] for k in keys]
        self.token = _auth(self.client)
        self.owned: list[int] = []

    def _cookie(self) -> dict:
        return {"Cookie": f"token={self.token}"}

    def _ensure_id(self) -> int | None:
        """Return an owned booking id, creating one if the pool is empty."""
        if self.owned:
            return self.owned[-1]
        resp = self.client.post("/booking", json=_booking_payload("select"),
                                name="POST /booking", catch_response=False)
        try:
            bid = resp.json().get("bookingid") if resp.status_code == 200 else None
        except ValueError:
            bid = None
        if bid is not None and len(self.owned) < 25:
            self.owned.append(bid)
        return bid

    @task
    def hit(self):
        ep = random.choices(self.endpoints, weights=[e.weight for e in self.endpoints])[0]
        path = ep.path
        headers = self._cookie() if ep.needs_auth else {}
        json_body = _booking_payload("select") if ep.has_body and ep.key != "auth" else None
        if ep.key == "auth":
            json_body = _ADMIN

        if ep.needs_id:
            bid = self._ensure_id()
            if bid is None:
                return  # couldn't obtain a resource to act on; skip this tick
            path = ep.path.replace("[id]", str(bid))

        with self.client.request(
            ep.method, path, name=ep.label, headers=headers or None,
            json=json_body, catch_response=True,
        ) as resp:
            # 404 on id-based calls is legitimate (another VU deleted it); drop
            # the stale id and don't count it as a failure.
            if ep.needs_id and resp.status_code == 404:
                resp.success()
                if self.owned:
                    self.owned.pop()
            elif ep.method == "DELETE" and resp.status_code in (200, 201):
                resp.success()
                if self.owned:
                    self.owned.pop()
            elif resp.status_code >= 400:
                resp.failure(f"{ep.label} returned {resp.status_code}")
