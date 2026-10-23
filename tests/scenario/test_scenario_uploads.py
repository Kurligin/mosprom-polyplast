"""Вложения: загрузка через фейковый API, кэш токена по sha256, повтор на attachment.not.ready."""

from types import SimpleNamespace

import pytest

from app.bot import uploads
from app.bot.api import MaxApiError
from app.scenario import mockups

pytestmark = pytest.mark.asyncio


class DictRepo:
    def __init__(self):
        self.settings = {}

    async def get_setting(self, key):
        return self.settings.get(key)

    async def set_setting(self, key, value):
        self.settings[key] = value


async def test_image_token_cached_by_content(api, sender, tmp_path):
    repo = DictRepo()
    path = mockups.ensure_asset("img_04.png")
    first = await uploads.attachment(path, repo=repo, sender=sender)
    assert first == {"type": "image", "payload": {"token": "img-img_04.png"}}
    # тот же файл под другим именем — без повторной загрузки
    copy = tmp_path / "copy.png"
    copy.write_bytes(path.read_bytes())
    again = await uploads.attachment(copy, repo=repo, sender=sender)
    assert again == first and api.uploads == [("image", "img_04.png")]
    key = next(iter(repo.settings))
    assert key.startswith(uploads.CACHE_PREFIX) and len(key) <= 64


async def test_file_kind_and_token(api, sender):
    att = await uploads.attachment(mockups.ensure_asset("food_GTP.pdf"), repo=DictRepo(), sender=sender)
    assert att == {"type": "file", "payload": {"token": "file-food_GTP.pdf"}}
    assert uploads.kind_for("x.JPG") == "image" and uploads.kind_for("plan.docx") == "file"


async def test_no_bot_raises(tmp_path):
    p = tmp_path / "a.png"
    p.write_bytes(b"x")
    with pytest.raises(RuntimeError):
        await uploads.attachment(p, repo=DictRepo(), sender=None)


async def test_send_retries_until_attachment_ready(api, sender, not_ready):
    pauses = []

    async def fake_sleep(s):
        pauses.append(s)

    emp = SimpleNamespace(id=1, chat_id=500, max_user_id=100, status="active")
    api.fail_queue = [not_ready, not_ready]
    mids = await uploads.send(sender, None, "Файл", employee=emp,
                              attachments=[{"type": "file", "payload": {"token": "t"}}], sleep=fake_sleep)
    assert mids == ["mid1"] and pauses == [1.0, 2.0]

    api.fail_queue = [not_ready] * 10
    with pytest.raises(MaxApiError):
        await uploads.send(sender, None, "Файл", employee=emp, attachments=[], sleep=fake_sleep)


async def test_other_errors_not_retried(api, sender):
    emp = SimpleNamespace(id=1, chat_id=500, max_user_id=100, status="active")
    api.fail_queue = [MaxApiError(400, "proto.payload", "bad")]
    with pytest.raises(MaxApiError):
        await uploads.send(sender, None, "x", employee=emp, sleep=None)
