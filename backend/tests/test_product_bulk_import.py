"""POST /admin/products/bulk-import — adding many products from a CSV at once."""

import uuid


def _import(client, headers, rows, update_existing=False):
    return client.post("/admin/products/bulk-import", json={"rows": rows, "update_existing": update_existing}, headers=headers)


def _products(client, headers):
    resp = client.get("/admin/products/", headers=headers)
    assert resp.status_code == 200, resp.text
    return {p["name"]: p for p in resp.json()}


def test_creates_many_products_in_one_go(client, signed_up_admin, auth_headers):
    resp = _import(client, auth_headers, [
        {"name": "Basmati Rice 5kg", "price": "649", "category": "Grocery", "stock_quantity": "40", "cost_price": "520"},
        {"name": "Toor Dal 1kg", "price": "₹1,299.50", "description": "Unpolished", "is_active": "yes"},
        {"name": "Old Stock Item", "price": "10", "is_active": "no"},
    ])
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert (body["created_count"], body["updated_count"], body["failure_count"]) == (3, 0, 0)
    assert all(r["success"] and r["action"] == "created" for r in body["results"])

    products = _products(client, auth_headers)
    assert products["Basmati Rice 5kg"]["price"] == 649 and products["Basmati Rice 5kg"]["stock_quantity"] == 40
    assert products["Toor Dal 1kg"]["price"] == 1299.5  # currency symbol and thousands comma accepted
    assert products["Old Stock Item"]["is_active"] is False


def test_bad_rows_are_reported_individually_and_good_rows_still_import(client, signed_up_admin, auth_headers):
    body = _import(client, auth_headers, [
        {"name": "Good One", "price": "10"},
        {"name": "", "price": "10"},
        {"name": "No Price"},
        {"name": "Text Price", "price": "abc"},
        {"name": "Negative", "price": "-5"},
        {"name": "Half Stock", "price": "5", "stock_quantity": "2.5"},
        {"name": "Bad Flag", "price": "5", "is_active": "maybe"},
        {"name": "Evil Image", "price": "5", "image_url": "javascript:alert(1)"},
        {"name": "Another Good", "price": "20", "image_url": "https://example.com/a.jpg"},
    ]).json()
    assert body["created_count"] == 2 and body["failure_count"] == 7
    errors = {r["row_number"]: r["error"] for r in body["results"] if not r["success"]}
    assert "name is required" in errors[2]
    assert "price is required" in errors[3]
    assert "isn't a number" in errors[4]
    assert "negative" in errors[5]
    assert "whole number" in errors[6]
    assert "yes/no" in errors[7]
    assert "image_url" in errors[8]
    assert set(_products(client, auth_headers)) == {"Good One", "Another Good"}


def test_duplicates_are_skipped_unless_update_existing_is_on(client, signed_up_admin, auth_headers):
    _import(client, auth_headers, [{"name": "Milk 1L", "price": "60", "category": "Dairy", "stock_quantity": "10"}])

    skipped = _import(client, auth_headers, [{"name": "milk 1l", "price": "99"}]).json()  # case-insensitive match
    assert skipped["failure_count"] == 1 and "already exists" in skipped["results"][0]["error"]
    assert _products(client, auth_headers)["Milk 1L"]["price"] == 60

    updated = _import(client, auth_headers, [{"name": "Milk 1L", "price": "65"}], update_existing=True).json()
    assert updated["updated_count"] == 1 and updated["results"][0]["action"] == "updated"
    milk = _products(client, auth_headers)["Milk 1L"]
    assert milk["price"] == 65
    assert milk["category"] == "Dairy" and milk["stock_quantity"] == 10  # blank cells leave old values alone


def test_the_same_name_twice_in_one_file_only_creates_one(client, signed_up_admin, auth_headers):
    body = _import(client, auth_headers, [{"name": "Twin", "price": "1"}, {"name": "Twin", "price": "2"}]).json()
    assert body["created_count"] == 1 and body["failure_count"] == 1
    assert _products(client, auth_headers)["Twin"]["price"] == 1


def test_products_are_matched_only_within_your_own_organization(client, signed_up_admin, auth_headers):
    _import(client, auth_headers, [{"name": "Shared Name", "price": "10"}])
    other = client.post("/auth/signup", json={
        "username": f"other_{uuid.uuid4().hex[:6]}", "email": f"other_{uuid.uuid4().hex[:6]}@example.com",
        "password": "correct-horse-battery", "role": "admin", "display_name": "Other Admin", "org_name": f"Org_{uuid.uuid4().hex[:6]}",
    }).json()
    other_headers = {"Authorization": f"Bearer {other['access_token']}"}

    # The other org can add its own "Shared Name" — and can't overwrite ours.
    body = _import(client, other_headers, [{"name": "Shared Name", "price": "999"}], update_existing=True).json()
    assert body["created_count"] == 1 and body["updated_count"] == 0
    assert _products(client, auth_headers)["Shared Name"]["price"] == 10


def test_requires_login_and_a_staff_role_that_can_manage_products(client, signed_up_admin):
    assert client.post("/admin/products/bulk-import", json={"rows": []}).status_code == 401
    agent = client.post("/auth/signup", json={
        "username": f"agent_{uuid.uuid4().hex[:6]}", "email": f"agent_{uuid.uuid4().hex[:6]}@example.com",
        "password": "correct-horse-battery", "role": "agent", "display_name": "An Agent", "invite_code": signed_up_admin["org_invite_code"],
    }).json()
    resp = _import(client, {"Authorization": f"Bearer {agent['access_token']}"}, [{"name": "X", "price": "1"}])
    assert resp.status_code == 403


def test_row_limit_is_enforced(client, signed_up_admin, auth_headers):
    resp = _import(client, auth_headers, [{"name": f"P{i}", "price": "1"} for i in range(1001)])
    assert resp.status_code == 422
