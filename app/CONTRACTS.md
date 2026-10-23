# Контракты модулей

Как модули бэкенда связаны друг с другом. Если меняешь сигнатуру из этого файла — обнови файл.

## Общее

- **Бот не флудит.** Ответ на кнопку меняет то же сообщение (`cb.replace` / `cb.mark` из `app/bot/hooks.py`). Следующий вопрос опроса — в том же сообщении. Новое сообщение — только для нового повода: шаг сценария, ответ HR, ответ на вопрос.
- **Тексты бота** — простые живые фразы в тоне Корсика из ТЗ: без длинных тире «—», канцелярита и лишних эмодзи. Тексты шагов сценария из ТЗ — дословно. «Ты/вы» — пары `(ты, вы)` и `app.bot.texts.tv(emp, pair)`.
- **БД:** `from app.db.session import async_session`; модели — `app/db/models.py`. Миграции и правки моделей делает координатор. Если нужно поле — используй JSONB (`Employee.dialog_data`, `ScenarioStep.extra`) и напиши об этом в отчёте.
- **Обработчики бота** получают `repo` (`app.bot.repo.SqlRepo`). Сессия — `repo.s`, коммит делает `runner` после события. Не коммить внутри обработчика.
- **Вне обработчиков** (планировщик, API) — своя сессия и `app.bot.runtime`:
  - `send(repo, text, employee=|staff=|chat_id=, attachments=)` → список `mid` или `None`;
  - `edit(mid, text, attachments)`;
  - `notify_hr_group(repo, text, attachments)`.
  Для журнала сообщений `repo = SqlRepo(session)`. Если бот выключен, функции возвращают `None` и ничего не ломают.
- **Подключение к боту** — `app/<модуль>/hooks.py`, список модулей — `app/plugins.py`. Регистрация:
  - `hooks.on_callback(prefix)`, `on_state(prefix)`, `on_command(name)`, `pre_route`;
  - `on_free_text`, `on_onboarded`, `on_create_ticket`, `on_staff_text`.
- **Префиксы payload кнопок:**
  - `ob` — знакомство, `hr` — обращение, встроены в бота;
  - `sc` — сценарий, `sv` — опросы, `kb` — база знаний, `tk` — обращения, `mg` — руководитель.
  Payload — до 128 символов. `dialog_state` — `"<префикс>:<детали>"`.
- **Фоновые задачи:** `@app.jobs.job(name, seconds=… | cron={…})`. Время в cron — московское. Задача сама открывает сессию и коммитит.
- **Время:** в БД UTC (`timestamptz`). Часовой пояс площадки — `Site.tz`. Машина времени — `Employee.time_shift_hours`; «сейчас» для новичка = `now + shift`.
- **Клавиатуры:** `app.bot.keyboards`: `keyboard(row, …)`, `callback(text, payload)`, `link(text, url)`, `open_app(text, web_app)`. Mini App открывается кнопкой `open_app(…, settings.max_bot_name)`.
- **Вложения:** `app.bot.uploads.attachment(path, kind) -> dict` — готовый элемент `attachments` (`image` | `file`); токен кэшируется в `Setting`.

## Сценарий — `app/scenario`

- `store.import_xlsx(session, path) -> ImportReport`. Пишет шаги в `scenario_steps` (ключ шага стабильный, повторный импорт обновляет шаги) и возвращает новые теги площадок. CLI: `python -m app.scenario.store <xlsx>` (`make import`).
- `engine.tick()` — задача `scenario_tick` раз в минуту:
  - наступившие шаги → `deliveries` (ключ идемпотентности) → отправка;
  - шаг-опрос → `app.surveys.engine.start_survey`.
- **Знакомство в боте покрывает шаги «0» (описание и приветствие) и 1.1–1.4.** Движок их не отправляет и помечает доставленными.
- Шаг-опрос определяет модуль опросов: `survey_for_step(номер строки)`. Шаги 20/21 (доступы) и 36 тоже уходят в опросы.
- Дополнительно: `engine.gates(emp)`, `engine.step_title(row, emp)`, `time_travel(..., to="now")`, `upcoming_steps(..., now=None)`, `uploads.send(...)`, `store.warm_uploads(session)`.
- Ключ шага: `<момент>|<площадки>|<порядковый номер>`, например `wd1@1200|GTP|1`. Пометки доставки («знакомство в боте», «устарел», «перемотка», «письмо») пишутся в `Delivery.error`.
- В состоянии `sc:intro` вопрос со знаком «?» уходит в базу знаний, состояние сохраняется.
- `engine.adaptation_day(emp, now=None) -> int` — 1 = день выхода, ≤ 0 = до выхода. `engine.probation_days(emp) -> int`.
- `engine.upcoming_steps(session, emp, limit=5) -> list[{"at": iso, "title": str}]`.
- `engine.time_travel(session, emp, to: str) -> {"employee_id", "day", "message"}`, где `to` ∈ `-2 | 1 | 3 | 14 | 60 | end`.
- `engine.open_gate(session, emp, gate, *, file_path=None)`. Отмашки:
  - `recruiter_go` — «представлю тебя коллегам»;
  - `plan_ready` — адаптационный план, с файлом.
- Текст и фото «представления коллегам» — в `emp.dialog_data["intro"] = {"text", "photo_url", "at"}`.
- `/food`, кнопки `sc:`, `on_onboarded` — в `app/scenario/hooks.py`.

## Опросы — `app/surveys`, сигналы — `app/signals`

- Опросы описаны в `config/surveys.yaml` дословно по ТЗ, у каждого опроса указан номер шага сценария.
  - `surveys.engine.survey_for_step(step_number) -> str | None`.
  - `surveys.engine.start_survey(repo, emp, code)`: первый вопрос — новым сообщением, следующие — в том же сообщении.
- Напоминания о непройденном опросе — задача `survey_reminders`: на следующий день и через день.
- Кнопка «Напомнить руководителю про доступы» → `app.manager.service.remind_access(repo, emp)`.
- `signals.service.check_text(repo, emp, text, *, source, answer=None) -> list[Flag]`:
  - словарь красных флагов работает всегда;
  - ИИ-разбор — только при согласии `ai_analysis`, через `app.llm.gigachat.analyze_answer`.
- `signals.service.raise_flag(repo, emp, *, level, topic, quote, source, answer=None) -> Flag`. Создаёт обращение `tickets.service.create_ticket(…, source="flag")` и уведомление в HR-группу.

Уточнения по итогам раунда 2:
- `start_survey(repo, emp, code, *, force=False) -> str | None` возвращает `mid` или `None`, если опрос уже был или новичок не подключён.
- `survey_for_step` покрывает шаги 20, 21, 36–40, 45, 47, 53–55, 60–64. Часть из них в xlsx имеет тип `message`, поэтому опросы ищем только через `survey_for_step`, а не по `kind == "survey"`.
- Новые функции: `survey_history(emp)`, `pending_surveys(emp)`, `reset(emp, code=None)`, `remind_all(session, now=None)`.
- Опрос пройден, если у него есть `survey_history(emp)[code]["done_at"]`.
- `dialog_data["survey"]` — текущее прохождение, `dialog_data["surveys"][код]` — история. `dialog_state = "sv:<код>:<вопрос>:<режим>"`.
- Сигналы:
  - `raise_flag(…, ticket_id=None)`, `raise_hits(...)`, `employee_notice(repo, emp, flags, now=None)`;
  - `rules.detect(text, free_text=False)`, `rules.is_sensitive(text)`;
  - красный флаг → одно обращение (`source="flag"`, `level="red"`, `category`: safety / harassment / health / intent_to_leave);
  - жёлтый флаг — только запись `Flag`;
  - `pre_route` сигналов перехватывает свободный текст с красным флагом раньше базы знаний.
- `SurveyAnswer.topic` — тема вопроса (у текстовых её переписывает ИИ), `mood` — из варианта ответа или оценки.

## База знаний — `app/kb`

- `ingest.ingest_all(session)` — `docs/*.md`, справочные статьи сценария, черновик статьи «Доступ к корпоративному порталу». CLI: `python -m app.kb.ingest --all` (`make kb`).
- `ingest.ingest_file(session, path, *, title, is_kt, site_codes, uploaded_by, status="confirmed") -> (KbDocument, n_chunks)` — PDF / PPTX / MD. Новая версия документа заменяет старую.
- `ingest.index_hr_answer(session, hr_answer) -> KbChunk`. Ответ попадает в поиск, только когда `hr_answer.status == "confirmed"`.
- `search.search(session, query, *, site_code, limit=5) -> list[Hit]`. Гибридный поиск: полнотекстовый, векторный bge-m3, RRF, фильтр по площадке.
- `service.answer_question(ctx, repo, emp, text)` — `on_free_text`:
  - личный вопрос → обращение без ИИ;
  - поиск → ответ или выбор пункта в GigaChat;
  - «не знаю» → обращение;
  - всё пишется в `questions_log`.
- Карточка ответа:
  - подводка;
  - дословный пункт;
  - «Источник: …»;
  - кнопка «Связаться с HR» (`kb:hr:<question_log_id>`).

Уточнения по итогам раунда 2:
- `ingest_*` и `index_hr_answer` не коммитят — коммитит вызывающий код.
- `ingest_file` бросает `ValueError`, если формат не PDF/PPTX/MD или у PDF нет текстового слоя. API отвечает на это 400.
- У `Hit` есть дополнительные поля: `sim`, `fts_rank`, `vec_rank`, `chunk_key`, `kind` (doc | scenario | article | hr_answer), `title`, `path`, `point_no`, `focus`, `is_draft`. У `search` дополнительные параметры: `qvec=`, `pool=`.
- Форматы `chunk_key`: `pvtr:6.5.2`, `pvtr:разд3`, `pvtr:6.4.2/24` (строка длинного пункта), `scenario:documents@GTP`, `doc<id>:<п>`, `hr:<id>`.
- `KbDocument.doc_type`: `scenario` — статьи сценария, `article` — черновик HR.
- `questions_log.route`: answer | unknown | personal | hr. Обращение по кнопке «Связаться с HR» — `Ticket.source="button"`.
- Пороги ответа — в `config/kb.yaml`.
- `gigachat.choose_chunk(..., raise_errors=False, timeout=20.0)`, исключение `LlmUnavailable`.

## Обращения — `app/tickets`

- `service.create_ticket(ctx, repo, emp, text, *, source, category=None, is_personal=False, level=None) -> Ticket` (`level="red"` — красный флаг). Регистрируется через `hooks.on_create_ticket`. Обращение уходит закреплённому рекрутеру, в HR-группу — уведомление с кнопкой «Открыть в Mini App»; `Ticket.group_mid` хранит id уведомления.
- `service.reply(session, ticket, staff, text, *, add_to_kb=False, kb_site=None) -> Ticket`:
  - отправляет ответ новичку с подписью «Имя (HR)»;
  - первый ответ ставит статус `in_progress`;
  - `add_to_kb` создаёт `HrAnswer(status="draft")`.
- `pre_route`: текст новичка в течение 30 минут после ответа HR → в то же обращение и уведомление.
- `service.take(session, ticket, staff)`, `service.close(session, ticket, staff)`.

## Руководитель — `app/manager`, сводка — `app/reports`

- Задача `manager_tasks` (раз в 5 минут):
  - создаёт `ManagerTask`: `kind="meeting"`, `day` 3 / 30 / 60 и `kind="probation"` за 5 дней до конца испытательного (`day` — день адаптации даты встречи). Даты — по производственному календарю: 3-й рабочий день; 30-й и 60-й, если выходной — следующий рабочий; итоговая, если выходной — предыдущий рабочий;
  - в 10:00 дня встречи по часовому поясу площадки (с `time_shift_hours`) шлёт push с кнопками «Провёл» (`mg:done:<id>`) и «Открыть в Mini App» (если задан `MINIAPP_URL`). Пропущенный push догоняет 3 дня, итоговый — до конца испытательного. Синтетическим новичкам push не шлётся;
  - после нажатия «Провёл» push меняется на месте: «Встреча проведена ✓».
- `manager.service.tick(session, now=None) -> {"employees", "created", "sent"}` — то же, что задача, без коммита. Можно вызвать сразу после перемотки времени, чтобы не ждать 5 минут.
- `manager.service.mark_done(session, task, staff, *, edit_push=True) -> ManagerTask`. Правит push в чате руководителя. `PermissionError` — руководитель отмечает чужую встречу; HR и глава подбора могут отметить любую.
- `manager.service.remind_access(repo, emp) -> bool` — push руководителю «<Имя> пишет, что пока нет всех доступов». Руководитель не подключён — сообщение уходит в HR-группу. Повтор в течение 30 минут не дублирует push (`emp.dialog_data["access_reminded_at"]`).
- `manager.service.list_for_manager(session, staff) -> list[dict]` — экран «Мои новички»: `{id, full_name, site, work_type, start_date, day, probation_days, connected, synthetic, next_meeting: Meeting | null, meetings: Meeting[]}`, `Meeting` — как в `miniapp/src/api/types.ts`: `{id, day, title, date, done}`. Дописывает недостающие встречи (flush): вызывающий коммитит сессию.
- `manager.service.meetings_for(session, emp) -> list[Meeting]` — встречи для карточки новичка.
- `manager.service.adaptation_day(emp, now=None)`, `probation_days(emp)` — берут `scenario.engine`, пока его нет — считают сами.
- `reports.weekly.build(session, now=None) -> dict` — агрегаты за 4 недели: `kpi` (новички, средняя оценка, открытые и закрытые сигналы), `mood_rows` по площадкам, `topics` (топ-3), `low_data`, `synthetic`. Срез по площадке — только если ответили 5 и больше разных новичков. Мелкие площадки объединяются в строку с пометкой «мало данных»; если и вместе меньше 5 — только названия в `low_data`. Имён и цитат нет.
- `reports.render.render_png(report) -> bytes` — 1080 × 1350, Playwright, браузер переиспользуется.
- `reports.weekly.send(session, now=None) -> bool` — картинка с короткой подписью всем привязанным `Staff.role == "head_recruitment"`. Задача `weekly_report`: понедельник, 09:00. Подходит и для кнопки «Сформировать сводку» (`POST /api/demo/report`): `False` — глава подбора не привязан или бот выключен.

## API для Mini App — `app/api`

Контракт фронта — `miniapp/API.md`. Вход по `initData` (HMAC-SHA256, `auth_date` не старше 24 часов). Роль — по `staff.max_user_id`. Вне «МАКС» при `DEV_ALLOW_NO_INIT_DATA=true` запрос проходит как HR.
