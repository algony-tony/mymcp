import os

import httpx

BASE_URL = os.environ.get("MYMCP_COMPAT_URL", "http://127.0.0.1:8765")


def test_admin_requires_admin_token(admin_token):
    assert httpx.get(f"{BASE_URL}/admin/tokens").status_code == 401
    r = httpx.get(f"{BASE_URL}/admin/tokens", headers={"Authorization": "Bearer wrong"})
    assert r.status_code == 403


def test_admin_create_list_revoke(admin_token):
    h = {"Authorization": f"Bearer {admin_token}"}
    r = httpx.post(f"{BASE_URL}/admin/tokens", headers=h, json={"name": "compat-tmp", "role": "rw"})
    assert r.status_code == 200
    tok = r.json()["token"]
    assert r.json()["role"] == "rw"

    listing = httpx.get(f"{BASE_URL}/admin/tokens", headers=h).json()
    assert tok in listing

    assert httpx.delete(f"{BASE_URL}/admin/tokens/{tok}", headers=h).status_code == 200
    assert httpx.delete(f"{BASE_URL}/admin/tokens/{tok}", headers=h).status_code == 404


def test_admin_bad_role_400(admin_token):
    h = {"Authorization": f"Bearer {admin_token}"}
    r = httpx.post(f"{BASE_URL}/admin/tokens", headers=h, json={"name": "x", "role": "root"})
    assert r.status_code == 400
