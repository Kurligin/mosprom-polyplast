"""Вход без initData (DEV_ALLOW_NO_INIT_DATA) — только для запросов с этой машины, не через прокси и туннели.

Порты app и miniapp на стенде разработки открыты только на 127.0.0.1, prod-compose обход выключает.
"""

from pathlib import Path

import pytest
import yaml

from app.config import settings

ROOT = Path(__file__).resolve().parents[2]


@pytest.fixture
def dev(monkeypatch):
    monkeypatch.setattr(settings, "dev_allow_no_init_data", True)


@pytest.mark.asyncio
async def test_local_browser_still_works(client, world, dev):
    """Браузер разработчика на localhost: через прокси Vite и напрямую."""
    r = await client.get("/api/me")
    assert r.status_code == 200 and r.json()["role"] == "hr"
    r = await client.get("/api/me", headers={"Host": "app:8000", "Origin": "http://localhost:5173",
                                             "Referer": "http://localhost:5173/?role=manager",
                                             "X-Dev-Role": "manager", "Sec-Fetch-Site": "same-origin"})
    assert r.status_code == 200 and r.json()["role"] == "manager"
    r = await client.get("/api/me", headers={"Host": "127.0.0.1:8000"})
    assert r.status_code == 200


@pytest.mark.parametrize("headers", [
    {"X-Forwarded-For": "203.0.113.7"},                         # внешний прокси (Caddy, nginx)
    {"Forwarded": "for=203.0.113.7;proto=https"},
    {"X-Real-IP": "203.0.113.7"},
    {"CF-Connecting-IP": "203.0.113.7"},                        # туннель Cloudflare
    {"X-Forwarded-Proto": "https"},
    {"X-Forwarded-Host": "korsik.example.ru"},
    {"Host": "korsik.example.ru"},                              # запрос по внешнему адресу
    {"Host": "192.168.1.2:8000"},                               # по адресу в локальной сети (Wi-Fi площадки)
    {"Host": "192.168.1.2:8000", "X-Dev-Role": "head"},
    {"Referer": "https://abc-def.trycloudflare.com/"},          # Mini App открыта через туннель
    {"Origin": "https://evil.example"},                         # чужой сайт в браузере разработчика
    {"Sec-Fetch-Site": "cross-site"},
])
@pytest.mark.asyncio
async def test_bypass_refused_for_remote_requests(client, world, dev, headers):
    r = await client.get("/api/me", headers=headers)
    assert r.status_code == 401, (headers, r.text)


@pytest.mark.asyncio
async def test_cross_site_post_without_body_refused(client, world, dev):
    """POST без тела с чужого сайта (без preflight) не закрывает обращение от имени HR."""
    r = await client.post("/api/tickets/1/close", headers={"Origin": "https://evil.example"})
    assert r.status_code == 401
    r = await client.post("/api/demo/report", headers={"Origin": "null"})
    assert r.status_code == 401


@pytest.mark.asyncio
async def test_init_data_still_works_behind_proxy(client, world, dev):
    """Через прокси вход по подписи initData работает как обычно."""
    from tests.api.conftest import auth

    r = await client.get("/api/me", headers={**auth(world.hr), "X-Forwarded-For": "203.0.113.7",
                                             "Host": "korsik.example.ru"})
    assert r.status_code == 200 and r.json()["role"] == "hr"


def test_public_peer_refused(dev):
    """Прямое подключение с внешнего адреса (порт опубликован наружу) — без обхода."""
    from app.api import auth as A

    assert A.dev_bypass_allowed(_req({"host": "localhost:8000"}, client=("8.8.8.8", 5555))) is False
    assert A.dev_bypass_allowed(_req({"host": "localhost:8000"}, client=("172.18.0.5", 5555))) is True
    assert A.dev_bypass_allowed(_req({"host": "localhost:8000"}, client=("127.0.0.1", 5555))) is True


def _req(headers: dict, client=("127.0.0.1", 1)):
    from starlette.requests import Request

    scope = {"type": "http", "method": "GET", "path": "/api/me", "headers": [
        (k.lower().encode(), v.encode()) for k, v in headers.items()], "client": client, "query_string": b""}
    return Request(scope)


def _compose(name: str) -> dict:
    class Loader(yaml.SafeLoader):
        pass

    Loader.add_constructor("!reset", lambda loader, node: None)
    Loader.add_constructor("!override", lambda loader, node: loader.construct_sequence(node))
    return yaml.load((ROOT / name).read_text(encoding="utf-8"), Loader=Loader)


def test_dev_compose_ports_only_on_localhost():
    services = _compose("docker-compose.yml")["services"]
    for name in ("app", "miniapp", "postgres"):
        for port in services[name].get("ports") or []:
            assert str(port).startswith("127.0.0.1:"), (name, port)


def test_prod_compose_disables_bypass_and_demo():
    app = _compose("docker-compose.prod.yml")["services"]["app"]
    env = app["environment"]
    assert str(env["DEV_ALLOW_NO_INIT_DATA"]).lower() == "false"
    assert str(env["DEMO_ENABLED"]).lower() in ("false", "${demo_enabled:-false}")
