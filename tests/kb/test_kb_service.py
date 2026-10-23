"""Маршрут вопроса и карточка ответа: без БД и GigaChat (поиск и LLM подменены)."""

import itertools
from types import SimpleNamespace

import pytest

from app.bot.hooks import Callback
from app.db.models import KbChunk, QuestionLog
from app.kb import route as R
from app.kb import service as S
from app.kb.search import Hit


def hit(cid, sim, *, kt=False, fts=None, vec=None, text=None, label="ПВТР, п. 6.6.1, стр. 15", key=None,
        status="confirmed", kind="doc"):
    return Hit(chunk_id=cid, text=text or f"6.6.1. Пункт {cid}.", source_label=label, score=1.0 / cid, is_kt=kt,
               status=status, sim=sim, fts_rank=fts, vec_rank=vec, chunk_key=key or f"pvtr:{cid}", kind=kind)


# --- plan: чистая логика порогов ---


def test_plan_thresholds():
    assert R.plan([], 0.68, 0.5)[0] == "escalate"
    assert R.plan([hit(1, 0.7)], 0.68, 0.5) == ("answer", [R.plan([hit(1, 0.7)], 0.68, 0.5)[1][0]])
    assert R.plan([hit(1, 0.45)], 0.68, 0.5)[0] == "escalate"
    action, cands = R.plan([hit(1, 0.6, kt=True), hit(2, 0.55)], 0.68, 0.5)
    assert action == "ask_llm" and [h.chunk_id for h in cands] == [2]  # КТ в LLM не уходит


def test_plan_best_similarity_not_first_rank():
    # уверенность — лучшая близость в топе: пункт со второго места выше порога
    action, hs = R.plan([hit(1, 0.55), hit(2, 0.72)], 0.68, 0.5)
    assert action == "answer" and hs[0].chunk_id == 2


def test_plan_kt_only_needs_agreement():
    assert R.plan([hit(1, 0.55, kt=True, fts=1, vec=1)], 0.68, 0.5, agree_direct=False)[0] == "answer"
    assert R.plan([hit(1, 0.55, kt=True, fts=2, vec=1)], 0.68, 0.5)[0] == "escalate"


# --- фейки бота ---


class FakeSession:
    def __init__(self):
        self.rows: dict = {}
        self._id = itertools.count(1)
        self.sent_text: str | None = None

    def add(self, obj):
        if getattr(obj, "id", None) is None:
            obj.id = next(self._id)
        self.rows[(type(obj), obj.id)] = obj

    async def flush(self):
        pass

    async def get(self, model, pk):
        return self.rows.get((model, pk))

    async def execute(self, _stmt):
        return SimpleNamespace(scalar=lambda: self.sent_text)


class FakeSender:
    def __init__(self):
        self.sent, self.answers, self.typed = [], [], 0

    async def send(self, repo, text, *, attachments=None, **kw):
        self.sent.append({"text": text, "attachments": attachments, **kw})
        return ["mid-1"]

    async def typing(self, chat_id):
        self.typed += 1

    async def answer(self, callback_id, *, message=None, notification=None, key=None):
        self.answers.append({"message": message, "notification": notification})


@pytest.fixture
def env(monkeypatch):
    session = FakeSession()
    messages = []

    async def add_message(**kw):
        messages.append(kw)

    repo = SimpleNamespace(s=session, add_message=add_message)
    sender = FakeSender()
    ctx = SimpleNamespace(sender=sender)
    emp = SimpleNamespace(id=5, chat_id=500, max_user_id=100, site_code="GrPP", address_form="ty",
                          full_name="Анна Смирнова (синтетика)", first_name="Анна")
    tickets = []

    async def fake_ticket(ctx, repo, emp, text, source, **kw):
        t = SimpleNamespace(id=len(tickets) + 1, text=text, source=source, **kw)
        tickets.append(t)
        return t

    import app.bot.handlers as H

    monkeypatch.setattr(H, "create_hr_ticket", fake_ticket)
    monkeypatch.setattr(R, "thresholds", lambda: (0.68, 0.5))
    monkeypatch.setattr(S, "thresholds", lambda: (0.68, 0.5))
    monkeypatch.setattr(S, "agree_direct", lambda: True)
    return SimpleNamespace(session=session, repo=repo, sender=sender, ctx=ctx, emp=emp, tickets=tickets)


def _search(monkeypatch, hits):
    calls = []

    async def fake(session, q, *, site_code, limit=5):
        calls.append((q, site_code))
        return hits

    monkeypatch.setattr(S, "search", fake)
    return calls


def _llm(monkeypatch, result):
    import app.llm.gigachat as G

    seen = []

    async def fake(question, cands, names=None, **kw):
        seen.append(cands)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(G, "choose_chunk", fake)
    return seen


def _logs(env):
    return [o for (t, _), o in env.session.rows.items() if t is QuestionLog]


@pytest.mark.asyncio
async def test_personal_goes_to_hr_without_search(env, monkeypatch):
    calls = _search(monkeypatch, [])
    await S.answer_question(env.ctx, env.repo, env.emp, "Мне не пришла зарплата")
    assert calls == []
    assert env.tickets[0].source == "personal" and env.tickets[0].is_personal
    assert "личный вопрос" in env.sender.sent[0]["text"]
    assert _logs(env)[0].route == "personal" and _logs(env)[0].ticket_id == 1


@pytest.mark.asyncio
async def test_direct_answer_card_one_message(env, monkeypatch):
    calls = _search(monkeypatch, [hit(7, 0.74, text="6.5.2. Зарплата 29-го и 14-го.", label="ПВТР, п. 6.5.2, стр. 15")])
    seen = _llm(monkeypatch, None)
    await S.answer_question(env.ctx, env.repo, env.emp, "Когда платят зарплату?")
    assert calls == [("Когда платят зарплату?", "GrPP")]  # фильтр по площадке новичка
    assert seen == [] and env.sender.typed >= 1
    assert len(env.sender.sent) == 1
    msg = env.sender.sent[0]
    assert "> 6.5.2. Зарплата 29-го и 14-го." in msg["text"]
    assert "Источник: ПВТР, п. 6.5.2, стр. 15" in msg["text"]
    assert "Смотри" in msg["text"] and "—" not in msg["text"].split(">")[0]
    log = _logs(env)[0]
    assert log.route == "answer" and log.chunk_id == 7 and log.topic == "зарплата"
    btn = msg["attachments"][0]["payload"]["buttons"][0][0]
    assert btn == {"type": "callback", "text": "Связаться с HR", "payload": f"kb:hr:{log.id}"}


@pytest.mark.asyncio
async def test_vy_lead(env, monkeypatch):
    env.emp.address_form = "vy"
    _search(monkeypatch, [hit(7, 0.74)])
    await S.answer_question(env.ctx, env.repo, env.emp, "Во сколько обед?")
    assert "Смотрите" in env.sender.sent[0]["text"]


@pytest.mark.asyncio
async def test_middle_band_llm_chooses(env, monkeypatch):
    _search(monkeypatch, [hit(1, 0.6, kt=True), hit(2, 0.58), hit(3, 0.55)])
    seen = _llm(monkeypatch, 3)
    await S.answer_question(env.ctx, env.repo, env.emp, "скока длится обед")
    assert [c["chunk_id"] for c in seen[0]] == [2, 3]  # КТ не уходит
    assert "Пункт 3" in env.sender.sent[0]["text"]
    assert _logs(env)[0].chunk_id == 3


@pytest.mark.asyncio
async def test_llm_none_is_unknown_with_ticket(env, monkeypatch):
    _search(monkeypatch, [hit(2, 0.58)])
    _llm(monkeypatch, None)
    await S.answer_question(env.ctx, env.repo, env.emp, "Какой пароль от вайфая?")
    assert env.tickets[0].source == "unknown"
    assert "не знаю" in env.sender.sent[0]["text"] and "передал вопрос HR" in env.sender.sent[0]["text"]
    assert _logs(env)[0].route == "unknown" and _logs(env)[0].ticket_id == 1


@pytest.mark.asyncio
async def test_llm_down_fallback(env, monkeypatch):
    from app.llm.gigachat import LlmUnavailable

    monkeypatch.setattr(S, "kb_config", lambda: {"llm_fallback_threshold": 0.6})
    _search(monkeypatch, [hit(2, 0.63)])
    _llm(monkeypatch, LlmUnavailable("timeout"))
    await S.answer_question(env.ctx, env.repo, env.emp, "Сколько длится испытательный срок?")
    assert "Пункт 2" in env.sender.sent[0]["text"] and not env.tickets


@pytest.mark.asyncio
async def test_low_score_unknown(env, monkeypatch):
    _search(monkeypatch, [hit(2, 0.4)])
    await S.answer_question(env.ctx, env.repo, env.emp, "Где выдают спецовку?")
    assert env.tickets and _logs(env)[0].route == "unknown"


@pytest.mark.asyncio
async def test_draft_and_kt_marks(env, monkeypatch):
    _search(monkeypatch, [hit(9, 0.8, label="Черновик HR, ждёт подтверждения", status="draft", kind="article")])
    await S.answer_question(env.ctx, env.repo, env.emp, "Как получить доступ к порталу?")
    assert "черновик" in env.sender.sent[0]["text"] and "Источник: Черновик HR, ждёт подтверждения" in \
        env.sender.sent[0]["text"]
    env.sender.sent.clear()
    _search(monkeypatch, [hit(4, 0.8, kt=True, label="Положение о коммерческой тайне, слайд 5")])
    await S.answer_question(env.ctx, env.repo, env.emp, "Что такое коммерческая тайна?")
    assert "грифом КТ" in env.sender.sent[0]["text"]


@pytest.mark.asyncio
async def test_small_talk_no_ticket(env, monkeypatch):
    calls = _search(monkeypatch, [])
    await S.answer_question(env.ctx, env.repo, env.emp, "спасибо!")
    await S.answer_question(env.ctx, env.repo, env.emp, "ок")
    assert calls == [] and not env.tickets and not _logs(env)
    assert [m["text"] for m in env.sender.sent] == ["Пожалуйста! Если появится ещё вопрос, пиши."]


@pytest.mark.asyncio
async def test_contact_hr_button_replaces_card(env, monkeypatch):
    _search(monkeypatch, [hit(7, 0.74)])
    await S.answer_question(env.ctx, env.repo, env.emp, "Когда платят зарплату?")
    card = env.sender.sent[0]["text"]
    log = _logs(env)[0]
    chunk = KbChunk(id=7, text="6.6.1. Пункт 7.", source_label="ПВТР, п. 6.6.1, стр. 15", is_kt=False)
    env.session.add(chunk)
    env.session.sent_text = card  # карточка в журнале сообщений
    cb = Callback(ctx=env.ctx, repo=env.repo, callback_id="cb1", payload=f"kb:hr:{log.id}", label="Связаться с HR",
                  orig_text="(без разметки)", mid="mid-1", chat_id=500, user_id=100)
    await S.on_button(cb, env.emp)
    assert env.tickets[0].source == "button" and env.tickets[0].text.startswith("Когда платят зарплату?")
    new_text = env.sender.answers[0]["message"]["text"]
    assert new_text.startswith(card) and new_text.endswith("Передал вопрос HR. Ответ придёт сюда.")
    assert env.sender.answers[0]["message"]["attachments"] == []  # кнопка убрана
    assert log.route == "hr" and log.ticket_id == 1


@pytest.mark.asyncio
async def test_contact_hr_foreign_question(env, monkeypatch):
    env.session.add(QuestionLog(id=99, employee_id=777, question="чужой", route="answer"))
    cb = Callback(ctx=env.ctx, repo=env.repo, callback_id="cb1", payload="kb:hr:99", label=None, orig_text="",
                  mid=None, chat_id=500, user_id=100)
    await S.on_button(cb, env.emp)
    assert not env.tickets and env.sender.answers[0]["notification"] == "Не нашёл этот вопрос"


def test_topic_for_hit_path():
    h = hit(1, 0.7)
    h.path = "Раздел 6 → 6.7. Время отдыха → п. 6.7.4"
    assert S.topic_for("а сколько дней дают", h) == "Время отдыха"
    h.path = "Слайд 8. Деловые подарки"
    assert S.topic_for("что можно принять", h) == "Деловые подарки"
