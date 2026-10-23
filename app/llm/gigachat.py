"""Клиент GigaChat: выбор пункта из найденных и разбор свободного ответа опроса.

ИИ не пишет норму: choose_chunk только возвращает номер найденного пункта или None.
Фрагменты с пометкой КТ в LLM не уходят. Тексты о здоровье (app.privacy.health) не уходят вовсе.
Перед вызовом маскируются ФИО всех сотрудников из БД (app.privacy.names), телефоны, e-mail и номера документов.
В лог — без текстов.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import re
import sys
import time
from pathlib import Path
from types import SimpleNamespace

from app.privacy import names as privacy_names
from app.privacy.health import health_match
from app.privacy.mask import mask_with_classes

log = logging.getLogger("llm_log")

# У физлица в GigaChat — один поток: все вызовы строго по очереди
_SEM = asyncio.Semaphore(1)
_client = None

TOPICS = [
    "доступы",
    "документы",
    "наставник",
    "руководитель",
    "коллектив",
    "понимание задач",
    "нагрузка",
    "быт и транспорт",
    "обучение",
    "охрана труда",
    "другое",
]
MOODS = ["негатив", "нейтрально", "позитив"]


def _is_health(text: str, *, rules_list: bool = False) -> bool:
    """Тексты о здоровье в LLM не отправляем (plan.md, dev-plan 4.10): общий детектор app.privacy.health.

    rules_list=True — ещё и список sensitive из config/flag_rules.yaml (его правит HR), как в сигналах.
    Для ответов опросов: там он шире («операция», «давление»), а для вопросов в базу знаний отрезал бы
    общие вопросы о порядке («больничный кто оплачивает?»), где данных о здоровье нет.
    """
    if health_match(text):
        return True
    if not rules_list:
        return False
    try:
        from app.signals.rules import is_sensitive

        return bool(is_sensitive(text))
    except Exception as e:  # noqa: BLE001 — нет файла правил: хватает общего детектора
        log.debug("flag_rules.sensitive недоступен: %s", type(e).__name__)
        return False


async def _all_names(names: list[str] | None) -> list[str]:
    """Имена от вызывающего кода + ФИО всех сотрудников из БД (кэш)."""
    try:
        known = await privacy_names.known_names()
    except Exception as e:  # noqa: BLE001
        log.warning("Имена сотрудников для маскирования недоступны: %s", type(e).__name__)
        known = []
    return [*(names or []), *known]


def _settings():
    """Настройки из app.config; если модуля нет или нет pydantic-settings — из окружения."""
    try:
        from app.config import settings  # type: ignore

        return settings
    except Exception:  # noqa: BLE001
        return SimpleNamespace(
            gigachat_credentials=os.environ.get("GIGACHAT_CREDENTIALS", ""),
            gigachat_scope=os.environ.get("GIGACHAT_SCOPE", "GIGACHAT_API_PERS"),
            gigachat_model=os.environ.get("GIGACHAT_MODEL", "GigaChat-2"),
            ca_bundle_file=os.environ.get("CA_BUNDLE_FILE", "certs/russian_trusted_ca_bundle.pem"),
        )


def get_client():
    global _client
    if _client is None:
        from gigachat import GigaChat

        s = _settings()
        ca = s.ca_bundle_file or None
        if ca and not Path(ca).is_absolute():
            ca = str(Path(__file__).resolve().parents[2] / ca)
        _client = GigaChat(
            credentials=s.gigachat_credentials,
            scope=s.gigachat_scope or "GIGACHAT_API_PERS",
            model=s.gigachat_model or None,
            ca_bundle_file=ca,
            verify_ssl_certs=True,
            timeout=60,
        )
    return _client


_tasks: set = set()


def _log(kind: str, classes: set[str], started: float, ok: bool, **extra) -> None:
    # Только метаданные: время, тип вызова, классы данных. Никаких текстов.
    ms = int((time.monotonic() - started) * 1000)
    log.info(
        "llm_call type=%s classes=%s ms=%d ok=%s %s",
        kind,
        ",".join(sorted(classes)) or "-",
        ms,
        ok,
        " ".join(f"{k}={v}" for k, v in extra.items()),
    )
    if "pytest" in sys.modules:
        return
    try:
        task = asyncio.get_running_loop().create_task(_persist(kind, sorted(classes), ms, ok))
    except RuntimeError:
        return
    _tasks.add(task)
    task.add_done_callback(_tasks.discard)


async def _persist(kind: str, classes: list[str], ms: int, ok: bool) -> None:
    """Строка в llm_log (своя сессия): тип вызова, классы данных, время. Текстов нет."""
    try:
        from app.db.models import LlmLog
        from app.db.session import async_session

        async with async_session() as session:
            session.add(LlmLog(call_type=kind, data_classes=classes, model=_model_name(),
                               latency_ms=ms, ok=ok))
            await session.commit()
    except Exception as e:  # noqa: BLE001 — журнал не должен ломать ответ
        log.debug("llm_log не записан: %s", e)


def _provider() -> str:
    return (getattr(_settings(), "llm_provider", "") or "gigachat").lower()


def _model_name() -> str | None:
    s = _settings()
    if _provider() == "openai":
        return getattr(s, "llm_model", "") or None
    return s.gigachat_model or None


async def _complete_openai(system: str, user: str, temperature: float) -> str:
    """OpenAI-совместимый /chat/completions: локальная модель в закрытом контуре (vLLM, Ollama)."""
    import httpx

    s = _settings()
    base = (getattr(s, "llm_base_url", "") or "").rstrip("/")
    if not base:
        raise RuntimeError("LLM_BASE_URL не задан")
    headers = {"Authorization": f"Bearer {s.llm_api_key}"} if getattr(s, "llm_api_key", "") else {}
    body = {
        "model": getattr(s, "llm_model", "") or "default",
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "temperature": temperature,
        "max_tokens": 300,
    }
    async with httpx.AsyncClient(timeout=120) as client:
        resp = await client.post(f"{base}/chat/completions", json=body, headers=headers)
        resp.raise_for_status()
        return resp.json()["choices"][0]["message"]["content"] or ""


async def _complete(system: str, user: str, temperature: float = 0.01) -> str:
    if _provider() == "openai":
        async with _SEM:
            return await _complete_openai(system, user, temperature)

    from gigachat.models import Chat, Messages, MessagesRole

    chat = Chat(
        messages=[
            Messages(role=MessagesRole.SYSTEM, content=system),
            Messages(role=MessagesRole.USER, content=user),
        ],
        temperature=temperature,
        max_tokens=300,
    )
    async with _SEM:
        resp = await get_client().achat(chat)
    return resp.choices[0].message.content or ""


def parse_json_object(raw: str) -> dict | None:
    """Строгий разбор: первый JSON-объект из ответа (допускаем ```json-обёртку)."""
    if not raw:
        return None
    s = raw.strip()
    s = re.sub(r"^```(?:json)?\s*|\s*```$", "", s, flags=re.IGNORECASE)
    m = re.search(r"\{.*\}", s, re.DOTALL)
    if not m:
        return None
    try:
        obj = json.loads(m.group(0))
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


CHOOSE_SYSTEM = (
    "Ты помощник по внутренним документам компании. Тебе дают вопрос сотрудника и "
    "пронумерованные пункты документов. Выбери ОДИН пункт, который прямо отвечает на вопрос. "
    "Если ни один пункт не отвечает на вопрос прямо — верни null. Ничего не придумывай и не пересказывай. "
    'Ответь строго JSON без пояснений: {"chunk_id": <номер пункта или null>}'
)


class LlmUnavailable(Exception):
    """GigaChat не ответил (сеть, лимит, таймаут)."""


async def choose_chunk(question: str, candidates: list[dict], names: list[str] | None = None, *,
                       raise_errors: bool = False, timeout: float = 20.0) -> int | None:
    """Выбрать пункт из кандидатов [{chunk_id, text, is_kt?}] или None («нет подходящего»).

    raise_errors=True — при сбое GigaChat бросить LlmUnavailable (бот решит, что делать),
    иначе сбой = None. Вопрос на тему здоровья в LLM не отправляем: None, бот передаст вопрос HR.
    """
    safe = [c for c in candidates if not c.get("is_kt")]
    if not safe:
        return None
    if _is_health(question or ""):
        log.info("llm_call type=choose_chunk skipped=health")
        return None
    names = await _all_names(names)
    allowed = {str(c["chunk_id"]): c["chunk_id"] for c in safe}
    q, classes = mask_with_classes(question, names)
    body = "\n\n".join(f"[{c['chunk_id']}]\n{mask_with_classes(c['text'], names)[0]}" for c in safe)
    user = f"Вопрос: {q}\n\nПункты:\n{body}"
    classes |= {"question", "kb_chunks"}

    for attempt in range(2):  # один повтор при битом JSON
        started = time.monotonic()
        try:
            raw = await asyncio.wait_for(_complete(CHOOSE_SYSTEM, user), timeout)
        except Exception as e:
            _log("choose_chunk", classes, started, False, err=type(e).__name__)
            if raise_errors:
                raise LlmUnavailable(type(e).__name__) from e
            return None
        obj = parse_json_object(raw)
        if obj is None or "chunk_id" not in obj:
            _log("choose_chunk", classes, started, False, err="bad_json", attempt=attempt)
            continue
        cid = obj["chunk_id"]
        if cid is None or str(cid).strip().lower() in ("null", "none", ""):
            _log("choose_chunk", classes, started, True, result="none")
            return None
        key = str(cid).strip().strip("[]")
        if key in allowed:
            _log("choose_chunk", classes, started, True, result="chunk")
            return allowed[key]
        # номер не из кандидатов — считаем «нет подходящего»
        _log("choose_chunk", classes, started, False, err="unknown_id")
        return None
    return None


ANALYZE_SYSTEM = (
    "Ты анализируешь ответ нового сотрудника в опросе об адаптации. Определи: "
    f"topic — одна тема из списка: {', '.join(TOPICS)}; "
    f"mood — одно из: {', '.join(MOODS)}; "
    "quote — короткая ДОСЛОВНАЯ цитата из ответа, которая подтверждает тему (скопируй символ в символ). "
    'Ответь строго JSON без пояснений: {"topic": "...", "mood": "...", "quote": "..."}'
)


async def analyze_answer(text: str, names: list[str] | None = None) -> dict | None:
    """Разбор свободного ответа → {topic, mood, quote}. None — не отправляли или не разобрали."""
    if not text or not text.strip():
        return None
    if _is_health(text, rules_list=True):
        log.info("llm_call type=analyze_answer skipped=health")
        return None
    masked, classes = mask_with_classes(text, await _all_names(names))
    classes |= {"survey_answer"}
    for attempt in range(2):
        started = time.monotonic()
        try:
            raw = await _complete(ANALYZE_SYSTEM, f"Ответ сотрудника: {masked}")
        except Exception as e:  # noqa: BLE001
            _log("analyze_answer", classes, started, False, err=type(e).__name__)
            return None
        obj = parse_json_object(raw)
        if not obj:
            _log("analyze_answer", classes, started, False, err="bad_json", attempt=attempt)
            continue
        topic = str(obj.get("topic", "")).strip().lower()
        mood = str(obj.get("mood", "")).strip().lower()
        quote = str(obj.get("quote") or "").strip().strip("«»\"")
        result = {
            "topic": topic if topic in TOPICS else "другое",
            "mood": mood if mood in MOODS else "нейтрально",
            # цитата обязана быть подстрокой (маскированного) текста, иначе выбрасываем
            "quote": quote if quote and (quote in masked or quote in text) else None,
        }
        _log("analyze_answer", classes, started, True)
        return result
    return None
