"""Вход по initData «МАКС» и роли."""

import time
from urllib.parse import quote

import pytest

from app.api.auth import InitDataError, sign, validate_init_data
from app.config import settings
from tests.api.conftest import TOKEN, auth, init_data

# --- проверка подписи ---

def test_valid_signature_parses_user():
    data = validate_init_data(init_data(42), TOKEN)
    assert data["user"]["id"] == 42
    assert data["user"]["first_name"] == "Тест"


def test_wrong_token_rejected():
    with pytest.raises(InitDataError, match="подпись"):
        validate_init_data(init_data(42), "999:OTHER")


def test_tampered_user_rejected():
    raw = init_data(42).replace(quote('"id": 42', safe=""), quote('"id": 43', safe=""))
    with pytest.raises(InitDataError):
        validate_init_data(raw, TOKEN)


def test_no_hash_rejected():
    with pytest.raises(InitDataError, match="подпис"):
        validate_init_data(init_data(42, drop_hash=True), TOKEN)


def test_expired_rejected():
    old = int(time.time()) - 25 * 3600
    with pytest.raises(InitDataError, match="устарел"):
        validate_init_data(init_data(42, auth_date=old), TOKEN)


def test_fresh_within_day_ok():
    data = validate_init_data(init_data(42, auth_date=int(time.time()) - 23 * 3600), TOKEN)
    assert data["user"]["id"] == 42


def test_duplicate_param_rejected():
    with pytest.raises(InitDataError, match="повтор"):
        validate_init_data(init_data(42) + "&auth_date=1", TOKEN)


def test_full_url_fragment_with_double_encoding():
    """Фрагмент URL из документации: WebAppData закодирован ещё раз."""
    inner = init_data(7)
    fragment = f"WebAppData={quote(inner, safe='')}&WebAppPlatform=web"
    assert validate_init_data(fragment, TOKEN)["user"]["id"] == 7


def test_sign_matches_reference():
    """HMAC-SHA256(secret=HMAC-SHA256("WebAppData", token), data_check_string) в hex."""
    import hashlib
    import hmac

    data = {"auth_date": "1700000000", "user": '{"id":1}'}
    secret = hmac.new(b"WebAppData", TOKEN.encode(), hashlib.sha256).digest()
    expected = hmac.new(secret, b'auth_date=1700000000\nuser={"id":1}', hashlib.sha256).hexdigest()
    assert sign(data, TOKEN) == expected


# --- через HTTP ---

@pytest.mark.asyncio
async def test_me_with_valid_init_data(client, world):
    r = await client.get("/api/me", headers=auth(world.hr))
    assert r.status_code == 200
    assert r.json() == {"role": "hr", "name": "Тестова Анна Сергеевна", "id": world.hr.id}


@pytest.mark.asyncio
async def test_expired_init_data_401(client, world):
    old = int(time.time()) - 25 * 3600
    r = await client.get("/api/me", headers={"X-Max-Init-Data": init_data(world.hr.max_user_id, auth_date=old)})
    assert r.status_code == 401
    assert "устарели" in r.json()["detail"]


@pytest.mark.asyncio
async def test_unsigned_init_data_401(client, world):
    r = await client.get("/api/me", headers={"X-Max-Init-Data": init_data(world.hr.max_user_id, drop_hash=True)})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_unknown_user_403(client, world):
    r = await client.get("/api/me", headers={"X-Max-Init-Data": init_data(1)})
    assert r.status_code == 403


@pytest.mark.asyncio
async def test_no_init_data_401_in_prod(client, world):
    r = await client.get("/api/me")
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_no_init_data_dev_mode_is_hr(client, world, monkeypatch):
    monkeypatch.setattr(settings, "dev_allow_no_init_data", True)
    r = await client.get("/api/me")
    assert r.status_code == 200 and r.json()["role"] == "hr"
    r = await client.get("/api/me", headers={"X-Dev-Role": "manager"})
    assert r.json()["role"] == "manager"


@pytest.mark.asyncio
@pytest.mark.parametrize(("who", "role"), [("hr", "hr"), ("rec", "hr"), ("mgr", "manager"), ("head", "head")])
async def test_roles(client, world, who, role):
    r = await client.get("/api/me", headers=auth(getattr(world, who)))
    assert r.json()["role"] == role


@pytest.mark.asyncio
async def test_manager_has_no_tickets_kb_topics(client, world):
    h = auth(world.mgr)
    for path in ("/api/tickets", "/api/tickets?status=open", "/api/tickets/1", "/api/kb/documents", "/api/topics"):
        r = await client.get(path, headers=h)
        assert r.status_code == 403, path
    for path in ("/api/tickets/1/reply", "/api/tickets/1/take", "/api/tickets/1/close", "/api/demo/report"):
        r = await client.post(path, headers=h, json={"text": "x"})
        assert r.status_code == 403, path


@pytest.mark.asyncio
async def test_manager_sees_only_own_without_signals(client, world, session):
    from app.db.models import Flag

    session.add(Flag(employee_id=world.emp.id, level="red", topic="safety", quote="синтетика", source="survey"))
    await session.flush()
    r = await client.get("/api/employees", headers=auth(world.mgr))
    ids = [e["id"] for e in r.json()]
    assert world.emp.id in ids and world.other.id not in ids
    me = next(e for e in r.json() if e["id"] == world.emp.id)
    assert me["red_flag"] is False and me["pending_surveys"] == 0 and me["open_tickets"] == 0

    card = (await client.get(f"/api/employees/{world.emp.id}", headers=auth(world.mgr))).json()
    assert card["surveys"] == [] and card["tickets"] == []
    r = await client.get(f"/api/employees/{world.other.id}", headers=auth(world.mgr))
    assert r.status_code == 404

    hr_view = (await client.get(f"/api/employees/{world.emp.id}", headers=auth(world.hr))).json()
    assert hr_view["red_flag"] is True
