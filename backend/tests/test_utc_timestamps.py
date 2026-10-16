"""
Regression tests for the "all dates/times are wrong" bug: the API used to
send naive UTC datetimes with no timezone marker, which browsers read as
local time (5h30m off in India). Every datetime in a JSON response must
now carry an explicit UTC marker.
"""

import re
from datetime import datetime

from main import mark_naive_datetimes_as_utc

ISO_WITH_ZONE = re.compile(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?(Z|[+-]\d{2}:\d{2})$")


def test_helper_marks_naive_and_leaves_others_alone():
    text = '{"a": "2026-10-16T10:00:00", "b": "2026-10-16T10:00:00.123456", "c": "2026-10-16T10:00:00Z", "d": "2026-10-16T10:00:00+05:30", "e": "2026-10-16"}'
    out = mark_naive_datetimes_as_utc(text)
    assert '"a": "2026-10-16T10:00:00Z"' in out
    assert '"b": "2026-10-16T10:00:00.123456Z"' in out
    assert '"c": "2026-10-16T10:00:00Z"' in out and "ZZ" not in out
    assert '"d": "2026-10-16T10:00:00+05:30"' in out
    assert '"e": "2026-10-16"' in out


def test_api_timestamps_are_utc_marked_and_current(client, signed_up_admin, auth_headers):
    before = datetime.utcnow()
    resp = client.get("/auth/me", headers=auth_headers)
    assert resp.status_code == 200, resp.text
    # Any datetime fields on the user must be explicit-UTC strings.
    stamps = [v for v in resp.json().values() if isinstance(v, str) and re.match(r"\d{4}-\d{2}-\d{2}T", v)]
    for value in stamps:
        assert ISO_WITH_ZONE.match(value), value

    # A freshly created delivery's created/updated time round-trips as the real time.
    import uuid
    agent = client.post("/auth/signup", json={
        "username": f"tz_agent_{uuid.uuid4().hex[:6]}", "email": f"tz_{uuid.uuid4().hex[:6]}@example.com",
        "password": "correct-horse-battery", "role": "agent", "display_name": "Tz Agent",
        "invite_code": signed_up_admin["org_invite_code"],
    }).json()["user"]["id"]
    now = datetime.utcnow()
    created = client.post("/deliveries/", json={
        "id": str(uuid.uuid4()), "agent_id": agent, "order_id": "TZ-1", "status": "picked_up",
        "created_at": now.isoformat() + "Z", "updated_at": now.isoformat() + "Z",
    }, headers=auth_headers)
    assert created.status_code == 200, created.text
    body = created.json()
    assert ISO_WITH_ZONE.match(body["updated_at"]), body["updated_at"]
    parsed = datetime.fromisoformat(body["updated_at"].replace("Z", "+00:00")).replace(tzinfo=None)
    assert abs((parsed - now).total_seconds()) < 5
    assert parsed >= before.replace(microsecond=0)
