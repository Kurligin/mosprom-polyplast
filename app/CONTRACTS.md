# Контракты модулей

Как модули бэкенда связаны друг с другом. Если меняешь сигнатуру из этого файла — обнови файл.

## Общее

- **Бот не флудит.** Ответ на кнопку меняет то же сообщение (`cb.replace` / `cb.mark` из `app/bot/hooks.py`). Следующий вопрос опроса — в том же сообщении. Новое сообщение — только для нового повода: шаг сценария, ответ HR, ответ на вопрос.
- **Тексты бота** — простые живые фразы в тоне Корсика из ТЗ: без длинных тире «—», канцелярита и лишних эмодзи. Тексты шагов сценария из ТЗ — дословно. «Ты/вы» — пары `(ты, вы)` и `app.bot.texts.tv(emp, pair)`.
- **БД:** `from app.db.session import async_session`; модели — `app/db/models.py`. Миграции и правки моделей делает координатор. Если нужно поле — используй JSONB (`Employee.dialog_data`, `ScenarioStep.extra`) и напиши об этом в отчёте.
- **Обработчики бота** получают `repo` (`app.bot.repo.SqlRepo`). Сессия — `repo.s`, коммит делает `runner` после события. Не коммить внутри обработчика.
- **Старое или повторное нажатие** (двойной тап, кнопка в устаревшем сообщении) — `cb.toast(...)`, а не `cb.mark()`: `mark` берёт текст из снимка на момент нажатия и затирает уже обновлённое сообщение. `cb.replace(..., fmt=None)` — без разметки, когда в тексте чужие слова (рассказ новичка).
- **Команды новичка:** неизвестная команда («/cancel», «/help») — короткая подсказка про /hr и /food, не обращение HR, не вопрос в базу знаний и не ответ опроса. «/cancel» и «/отмена» после /hr — отмена.
- **Привязка сотрудника:** привязка в БД (flush) до «Готово». Аккаунт уже привязан к другой роли — в демо (`DEMO_ENABLED`) старая привязка снимается, иначе `ACCOUNT_TAKEN`. Аккаунт настоящего новичка сотрудником не становится; тестовый новичок может быть привязан и как сотрудник — кнопки `tk:`, `mg:`, `ch:hr` тогда идут от имени сотрудника.
- **Демо-роли (`/demo`, `app/bot/demo.py`):** только при `DEMO_ENABLED`, иначе `/demo` — неизвестная команда; `/start` без кода от непривязанного в демо показывает то же меню. Кнопки `dm:` работают и до привязки. Роли — только тестовые новички (`is_test`) и персонал с ФИО и ролью из `seed.seed.STAFF`; аккаунт настоящего сотрудника не переключается (`DEMO_REAL`). Перемотка — та же `engine.time_travel`, что у Mini App; сброс прогресса удаляет строки новичка по `employee_id` и начинает знакомство заново в том же сообщении.
- **Вне обработчиков** (планировщик, API) — своя сессия и `app.bot.runtime`:
  - `send(repo, text, employee=|staff=|chat_id=, attachments=)` → список `mid` или `None`;
  - `edit(mid, text, attachments)`;
  - `notify_hr_group(repo, text, attachments)`.
  Для журнала сообщений `repo = SqlRepo(session)`. Если бот выключен, функции возвращают `None` и ничего не ломают.
- **Подключение к боту** — `app/<модуль>/hooks.py`, список модулей — `app/plugins.py`. Регистрация:
  - `hooks.on_callback(prefix)`, `on_state(prefix)`, `on_command(name)`, `pre_route`, `pre_route_always`;
  - `on_free_text`, `on_onboarded`, `on_create_ticket`, `on_staff_text`.
  - Обработчик `on_state` вернул `False` — состояние этот текст не ждёт: бот ведёт его дальше (pre_route, база знаний).
  - `pre_route_always` проверяет текст до состояний диалога (/hr, знакомство, рассказ о себе), кроме ожидания ответа в опросе. Так красный флаг ловится всегда.
- **Блокировка новичка:** `app.bot.repo.lock_employee(session, emp)` перечитывает новичка с `FOR NO KEY UPDATE`. Зовём вне обработчика бота перед правкой `dialog_data` (планировщик, API): JSONB пишется целиком, иначе поздний коммит затрёт чужие ключи. Бот берёт ту же блокировку в `SqlRepo.employee_by_user` на время события.
- **Префиксы payload кнопок:**
  - `ob` — знакомство, `hr` — обращение, встроены в бота;
  - `sc` — сценарий, `sv` — опросы, `kb` — база знаний, `tk` — обращения, `mg` — руководитель.
  Payload — до 128 символов. `dialog_state` — `"<префикс>:<детали>"`.
- **Ожидания в `dialog_data`** (не в `dialog_state`, чтобы не стирать друг друга):
  - `hr_wait = {"at", "resume", "mid"?}` — после /hr ждём вопрос 30 минут; ожидание опроса (`resume`) возвращается после вопроса. «/hr текст» — сразу обращение; есть кнопка «Отмена» (`hr:cancel`); вопрос ушёл или отменён — у сообщения `mid` кнопку убираем;
  - `intro_wait = {"since", "asked"}` — ждём рассказ о себе и фото 48 часов (по времени новичка);
  - `ticket_window = {"ticket_id", "until", "relayed", "shift"}` — окно ответа HR; при другом `time_shift_hours` окна нет.
- **Фоновые задачи:** `@app.jobs.job(name, seconds=… | cron={…})`. Время в cron — московское. Задача сама открывает сессию и коммитит.
- **Время:** в БД UTC (`timestamptz`). Часовой пояс площадки — `Site.tz`. Машина времени — `Employee.time_shift_hours`; «сейчас» для новичка = `now + shift`.
- **Клавиатуры:** `app.bot.keyboards`: `keyboard(row, …)`, `callback(text, payload)`, `link(text, url)`, `open_app(text, web_app)`. Mini App открывается кнопкой `open_app(…, settings.max_bot_name)`.
- **Вложения:** `app.bot.uploads.attachment(path, kind) -> dict` — готовый элемент `attachments` (`image` | `file`); токен кэшируется в `Setting`.

## Сценарий — `app/scenario`

- `store.import_xlsx(session, path) -> ImportReport`. Пишет шаги в `scenario_steps` (ключ шага стабильный, повторный импорт обновляет шаги) и возвращает новые теги площадок. `ValueError` — файл не открывается или в нём нет шагов (пустой импорт выключил бы весь сценарий); API отвечает 400. CLI: `python -m app.scenario.store <xlsx>` (`make import`).
- `engine.tick()` — задача `scenario_tick` раз в минуту:
  - наступившие шаги → `deliveries` (ключ идемпотентности) → отправка;
  - шаг-опрос → `app.surveys.engine.start_survey`.
- **Знакомство в боте покрывает шаги «0» (описание и приветствие) и 1.1–1.4.** Движок их не отправляет и помечает доставленными.
- Шаг-опрос определяет модуль опросов: `survey_for_step(номер строки)`. Шаги 20/21 (доступы) и 36 тоже уходят в опросы.
- Дополнительно: `engine.gates(emp)`, `engine.step_title(row, emp)`, `time_travel(..., to="now")`, `upcoming_steps(..., now=None)`, `uploads.send(...)`, `store.warm_uploads(session)`.
- Ключ шага: `<момент>|<площадки>|<порядковый номер>`, например `wd1@1200|GTP|1`. Пометки доставки («знакомство в боте», «устарел», «перемотка», «письмо») пишутся в `Delivery.error`.
- Рассказ о себе ждём в `dialog_data["intro_wait"]` (старое состояние `sc:intro` переводится туда). Рассказом считаются фото и текст от 25 символов, не похожий на вопрос (в том числе «Как получить…», «Скажите…», «Есть ли…» без «?»). Вопросы и короткие реплики идут обычным маршрутом, ожидание сохраняется. Рассказ — одно сообщение: когда текст есть, ждём только фото, другие тексты в рассказ не дописываются и идут обычным маршрутом. Принятое сообщение — всегда с ответом новичку. Перемотка назад забывает рассказ вместе с `intro_post`. Фоновая отправка шагов после перемотки и отмашки (`_deliver_soon`) ждёт коммита API (`lock_employee`). У шага есть кнопка «Пропустить» (`sc:<delivery_id>:skip`).
- Дни сценария считаются от первого рабочего дня (`engine.first_day`), шаги «последний день испытательного» — на последний рабочий день срока. Реплики после опроса (65, 66) не устаревают, пока опрос не пройден, и сутки после прохождения.
- `engine.adaptation_day(emp, now=None) -> int` — 1 = день выхода, ≤ 0 = до выхода. `engine.probation_days(emp) -> int`.
- `engine.upcoming_steps(session, emp, limit=5) -> list[{"at": iso, "title": str}]`.
- `engine.timeline(session, emp, limit=5, *, now=None, plan=None) -> {"past", "upcoming", "now"}` — путь новичка для карточки Mini App: `past` — отправленные шаги (у синтетики в статусе active без отправок — шаги плана, время которых наступило, кроме писем и отмашек), `upcoming` — как `upcoming_steps`, `now` — «сейчас» новичка. Пункты — `{"at": iso, "title": str}`, один на момент.
- `engine.time_travel(session, emp, to: str) -> {"employee_id", "day", "message"}`, где `to` ∈ `-2 | 1 | 3 | 14 | 60 | end`. Перемотка закрывает окно ответа HR. У тестового новичка `now` забывает отправки, опросы и отмашки после настоящего «сейчас».
- `engine.open_gate(session, emp, gate, *, file_path=None)`. Отмашки:
  - `recruiter_go` — «представлю тебя коллегам»;
  - `plan_ready` — адаптационный план, с файлом.
- Текст и фото «представления коллегам» — в `emp.dialog_data["intro"] = {"text", "photo_url", "at"}`.
- `/food`, кнопки `sc:`, `on_onboarded` — в `app/scenario/hooks.py`.

## Опросы — `app/surveys`, сигналы — `app/signals`

- Опросы описаны в `config/surveys.yaml` дословно по ТЗ, у каждого опроса указан номер шага сценария.
  - `surveys.engine.survey_for_step(step_number) -> str | None`.
  - `surveys.engine.start_survey(repo, emp, code)`: первый вопрос — новым сообщением, следующие — в том же сообщении.
- Напоминания о непройденном опросе — задача `survey_reminders`: на следующий рабочий день и через рабочий день (производственный календарь), в выходные не пишем. `remind_all` коммитит после каждого новичка (по `Employee.id`): блокировка не держится до конца рассылки.
- Ответ кнопкой (`o`, `s`, `m`, `md`) на вопрос, где ответ уже записан (`survey.answer_id`), — всплывашка «Ответ уже записал»: второй ответ, флаг и обращение не создаются.
- Кнопка «Напомнить руководителю про доступы» → `app.manager.service.remind_access(repo, emp)`.
- `signals.service.check_text(repo, emp, text, *, source, answer=None) -> list[Flag]`:
  - словарь красных флагов работает всегда;
  - ИИ-разбор — только при согласии `ai_analysis`, через `app.llm.gigachat.analyze_answer`.
- `signals.service.raise_flag(repo, emp, *, level, topic, quote, source, answer=None) -> Flag`. Создаёт обращение `tickets.service.create_ticket(…, source="flag")` и уведомление в HR-группу.

Уточнения по итогам раунда 2:
- `start_survey(repo, emp, code, *, force=False) -> str | None` возвращает `mid` или `None`, если опрос уже был или новичок не подключён.
- `survey_for_step` покрывает шаги 20, 21, 36–40, 45, 47, 51–55, 60–64. Часть из них в xlsx имеет тип `message`, поэтому опросы ищем только через `survey_for_step`, а не по `kind == "survey"`.
- Новые функции: `survey_history(emp)`, `pending_surveys(emp)`, `reset(emp, code=None)`, `remind_all(session, now=None)`.
- Опрос пройден, если у него есть `survey_history(emp)[code]["done_at"]`.
- `dialog_data["survey"]` — текущее прохождение, `dialog_data["surveys"][код]` — история. `dialog_state = "sv:<код>:<вопрос>:<режим>"`.
- Сигналы:
  - `raise_flag(…, ticket_id=None)`, `raise_hits(...)`, `employee_notice(repo, emp, flags, now=None)`;
  - `rules.detect(text, free_text=False)`, `rules.is_sensitive(text)`;
  - красный флаг → одно обращение (`source="flag"`, `level="red"`, `category`: safety / harassment / health / intent_to_leave);
  - жёлтый флаг — только запись `Flag`;
  - `pre_route_always` сигналов перехватывает свободный текст с красным флагом раньше базы знаний и в любом состоянии;
  - повтор той же тревоги за 12 часов — в открытое обращение флага (сообщение в ленте, HR получает «Новый ответ»); обращение закрыто — новое.
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
- `ingest_file` бросает `ValueError`, если формат не PDF/PPTX/MD, файл битый или у PDF нет текстового слоя. API отвечает на это 400 и удаляет непринятый файл.
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
- Кнопка «Открыть в Mini App» в push руководителю — `open_app` с `payload="employee_<id>"`: Mini App открывает карточку новичка.
- `manager.service.mark_done(session, task, staff, *, edit_push=True) -> ManagerTask`. Правит push в чате руководителя. `PermissionError` — руководитель отмечает чужую встречу; HR и глава подбора могут отметить любую.
- `manager.service.remind_access(repo, emp) -> bool` — push руководителю «<Имя> пишет, что пока нет всех доступов». Руководитель не подключён — сообщение уходит в HR-группу. Повтор в течение 30 минут не дублирует push (`emp.dialog_data["access_reminded_at"]`).
- `manager.service.list_for_manager(session, staff) -> list[dict]` — экран «Мои новички»: `{id, full_name, site, work_type, start_date, day, probation_days, connected, synthetic, next_meeting: Meeting | null, meetings: Meeting[]}`, `Meeting` — как в `miniapp/src/api/types.ts`: `{id, day, title, date, done}`. Дописывает недостающие встречи (flush): вызывающий коммитит сессию.
- `manager.service.meetings_for(session, emp) -> list[Meeting]` — встречи для карточки новичка.
- `manager.service.adaptation_day(emp, now=None)`, `probation_days(emp)` — берут `scenario.engine`, пока его нет — считают сами.
- `reports.weekly.build(session, now=None, *, week_end=None) -> dict` — плюс `risk_zone` (`weekly.risk_zone(report, items, clusters)`): `{people: {total, high, medium, reasons, text}, topics: [{topic, site, count, prev, trend, trend_text, text}]}` — сколько новичков в зоне высокого риска и почему, 1–2 растущие темы; тема по площадке — только для площадки из среза, тема одного человека не попадает, имён и цитат нет. Модуль аналитики сломался — `risk_zone: None`, сводка уходит без блока. В подписи к картинке — одна строка `weekly.risk_caption`. Агрегаты за 4 недели: `kpi` (новички, средняя оценка, открытые и закрытые сигналы), `mood_rows` по площадкам, `topics` (топ-3: из ответов опросов — только текстовые и не позитивные, плюс темы `questions_log`), `low_data`, `synthetic`. Срез по площадке — только если ответили 5 и больше разных новичков. Мелкие площадки объединяются в строку с пометкой «мало данных»; если и вместе меньше 5 — только названия в `low_data`. Порог — для каждой цифры: средняя оценка — от 5 поставивших оценку, доля настроения — от 5 новичков с настроением, иначе `None`. Открытые сигналы — все незакрытые флаги, без окна. Имён и цитат нет.
- `reports.render.render_png(report) -> bytes` — 1080 × 1350, Playwright, браузер переиспользуется.
- `reports.weekly.send(session, now=None, *, week_end=None) -> bool` — картинка с короткой подписью всем привязанным `Staff.role == "head_recruitment"` (руководитель HR). Подходит и для кнопки «Сформировать сводку» (`POST /api/demo/report`): `False` — руководитель HR не привязан или бот выключен.
- `reports.weekly.send_due(session, now=None) -> bool` — сводка за прошедшую неделю, если её ещё не отправили (`Setting` `weekly_report_sent_week`). Задачи `weekly_report` (понедельник, 09:00) и `weekly_report_catchup` (раз в 30 минут — догон, если в понедельник приложение не работало).

## Аналитика — `app/analytics`

Роль «руководитель HR» (в кейсе — руководитель управления по подбору и развитию персонала) и HR: зоны риска, темы опросов, этапы, настроение. Только чтение, без коммитов.

- `risks(session, now=None, *, site=None) -> list[RiskItem]` — новички в адаптации (не `finished`, испытательный не закончился, «до выхода» тоже) с причинами. Сначала высокий уровень, внутри — больше причин и свежее событие. `RiskItem.to_dict()` — форма API: `{employee_id, full_name, site, position, day, probation_days, stage, level, reasons: [{code, level, label, text, at}], connected, synthetic, card, start_param}`.
- Правила — `app/analytics/rules.py`, чистые функции над `Facts` (тестируются без БД). Уровень `high`, если есть причина высокого уровня или три средних, иначе `medium`. Тексты причин — без рода («Думает об уходе», «Молчит 9 дней»):
  - `red_flag` (высокий) — открытый красный сигнал, кроме «намерение уйти»;
  - `intent` — сигнал «намерение уйти» (кнопка или текст), открытый или за 30 дней — высокий; «Не уверен» на вопрос `fin_stay` — средний;
  - `score_drop` (высокий) — средняя оценка опроса упала на 3+ к прошлому опросу со шкалами; `low_score` (средний) — последняя средняя ниже 6. Шкалы по ТЗ есть только в чеке 2-й недели и итоговом опросе. Средняя опроса считается, только когда опрос пройден (`done_at`) или отвечены все его шкалы;
  - `yellow_flag`, `missed_surveys` (2 опроса подряд без ответа, статусы — как в карточке, `EmployeeViews.surveys`), `silent` (молчит 7+ дней, а бот ждёт ответа на опрос), `meeting_overdue` (встреча просрочена больше чем на 3 рабочих дня), `ticket_waiting` (обращение без ответа HR больше суток: не закрыто и нет первого ответа, «взято в работу» — тоже) — средние;
  - `not_connected` — не подключился за день до выхода и позже, с 5-го дня — высокий; `bot_stopped` (высокий) — статус `stopped`.
- `clusters(session, weeks=4, now=None, *, site=None, until=None, quotes=2, site_min=5) -> list[dict]` — темы ответов опросов: упоминание — свой текст (не позитивный) или не позитивная кнопка, тема — `SurveyAnswer.topic` или тема вопроса, «другое» — не тема. `{topic, label, count, people, negative, prev, delta, trend: up|down|flat|new, trend_text, sites: [{code, count, prev, people, name?}], quotes: [{text, mood, at}], quotes_reason: few|no_text|hidden|None, synthetic}`. `cluster_view(...)` — то же плюс `low_data`. Приватность (порог 5, как в сводке): за окно ответили меньше 5 новичков (в срезе площадки или всего) — тем нет (`summary.clusters_low_data`); тема одного человека — не тема; в `sites` — площадки, где о теме пишут от `site_min` человек, остальные — `{code: "other", name: "Другие площадки"}`; цитаты — только у темы от 5 человек, без площадки, при последнем согласии `ai_analysis` = да, не о здоровье, не из ответов с `Flag.answer_id`, через `app.privacy.mask` с `known_names()`, и без слов, похожих на фамилию или отчество (`clusters.has_name`).
- `funnel(session, now=None, *, site=None, risky=None)` — `[{stage, label, count, risk, risk_high}]`: pre, week1 (дни 1–7), month1 (8–30), month2 (31–60), month3 (61+), done (испытательный закончился за 90 дней).
- `summary(session, now=None, *, site=None)` — всё для `GET /api/analytics`; настроение по площадкам и неделям — через `reports.weekly` (порог 5 ответивших на каждую цифру).
- Синтетика для правил, которые не видны на общих ответах, — `seed/risks_seed.py` (пять «историй риска», `dialog_data["story"]`; общие сиды их не трогают) и согласия синтетических новичков.

## API для Mini App — `app/api`

Контракт фронта — `miniapp/API.md`. Вход по `initData` (HMAC-SHA256, `auth_date` не старше 24 часов). Роль — по `staff.max_user_id`. Вне «МАКС» при `DEV_ALLOW_NO_INIT_DATA=true` запрос проходит как HR.

## Чаты команды — `app/chats`

- Таблица `team_chats` (модель `TeamChat`): `chat_id`, `title`, `link` (ссылка-приглашение), `members_count`, `audience` (`none` — никому, по умолчанию | `all` | `sites`), `sites`, `work_type` (`office` | `production` | `None` — всем), `for_intro`, `active`, `prompt_mid`, `info_at`. Название, ссылку и число участников бот берёт из `GET /chats/{id}` при добавлении и раз в час.
- **HR-группа — по-прежнему `Setting('hr_group_chat_id')`**: его читают `runtime.notify_hr_group` и модули. В `team_chats` флага нет. Новичков в HR-группу не добавляем и представления туда не публикуем.
- `bot_added` → `service.register(ctx, repo, chat_id, user)`: чат в учёт. HR-группа не выбрана — в группе сообщение с кнопкой «Сделать эту группу HR-группой» (`ch:hr:<chat_id>`), иначе короткое знакомство без кнопки. `bot_removed` → `service.unregister(repo, chat_id)`: `active = False`, была HR-группой — Setting сбрасывается. Каналы не учитываем.
- Кнопку `ch:hr` может нажать только привязанный `Staff` с ролью `hr` / `recruiter` / `head_recruitment`, остальным — всплывашка. Работает, только пока HR-группа не выбрана и только в последнем сообщении с кнопкой (`TeamChat.prompt_mid`); иначе сообщение теряет кнопку, HR-группа не меняется (сменить — в Mini App). Повторный `bot_added` второе сообщение с кнопкой не шлёт. Обработчик кнопок с атрибутом `anyone = True` получает и нажатия не подключённых к боту (`person=None`), `handlers.on_message_callback` их не отбрасывает.
- `service.set_hr_group(session, chat, *, sender=None, repo=None, pressed_mid=None)` — HR-группа из кнопки или Mini App: Setting, правила чата сбрасываются в `none`, кнопки «Сделать HR-группой» в других чатах меняются на месте. `service.update_chat(session, chat, *, fields, sender=None)` — правила из Mini App, `ValueError` — текст для HR.
- `service.add_to_chats(session, emp, *, sender, repo=None, now=None) -> {"added", "link", "failed", "retry"}` — `POST /chats/{id}/members` (`MaxApi.add_members`) в подходящие чаты, один раз на чат. Только живой подключившийся новичок после знакомства (`is_synthetic` не трогаем) и только с `engine.adaptation_day(emp) >= 1`. Итог — `dialog_data["chats"][str(chat_id)] = {"status": added | link | failed | retry, "at", "title", "error"?, "mid"?}`. «МАКС» не дал добавить — одно сообщение новичку «Вот чаты твоей команды…» с кнопками-ссылками на эти чаты (тогда `link`). Окончательный `failed` — только `add.participant.privacy` и `add.participant.not.found` (код берётся и из HTTP-ошибки); ошибка чата (бот не админ, 403) без ссылки — `retry`, повтор не чаще раза в `RETRY_AFTER` (10 минут), в карточке — как `failed` с причиной. Сеть и 5xx — повтор на следующем запуске. Задача `team_chats` коммитит добавления до вопроса о публикации: его сбой их не откатывает. Вызывающий держит блокировку новичка и коммитит.
- Когда: задача `team_chats` раз в 5 минут (`service.tick`) и сразу после знакомства, если день выхода уже наступил: `app.chats.hooks` оборачивает обработчик `on_onboarded` сценария (сначала шаги, потом чаты).
- «Представлю тебя коллегам»: `engine.on_intro` собрал рассказ (текст и фото) → `service.offer_intro(sender, repo, emp, lead)` — «Спасибо» и вопрос «опубликовать в чате команды?» одним сообщением, в нём же пост целиком (`intro_post_text`, без markdown): согласие — на конкретный текст; кнопки `ch:pub:<chat_id>` / `ch:nopub`. Нажатие принимается только в последнем вопросе (`intro_post.mid`) и про тот же рассказ (`intro_at`), иначе кнопки снимаются и ничего не публикуется. Чат из вопроса пропал или больше не подходит (`fits_intro`) — в другой чат без нового согласия не публикуем: то же сообщение спрашивает про подходящий чат, его нет — «не получилось». Подводка — с названием площадки из справочника (`site_label`: «площадка GTP», «офис «Лужники»»). Чата нет — обычное «Спасибо». Рассказ без фото (ожидание 48 часов истекло или «Пропустить») — вопрос задаёт задача `team_chats`. Ответ меняет то же сообщение; публикация — подводка «Знакомьтесь, это …», текст новичка дословно, фото (без markdown). Итог — `dialog_data["intro_post"] = {"status": asked | published | declined | failed, "chat_id", "mid", "intro_at", "post_mid"?}`; про тот же рассказ (`intro_at`) второй раз не спрашиваем.
- Основной чат для представления: активный, не HR-группа, с отметкой `for_intro` (подходит новичку или `audience = none`), иначе чат площадки новичка (`audience = sites`). Из нескольких — с самым узким кругом.
- Mini App: `GET /api/chats`, `PATCH /api/chats/{id}` (`app/api/chats.py`), в карточке новичка — `chats` (`service.card(session, emp)`): `[{chat_id, title, status: added | link | failed | pending | intro, at, reason, link, intro}]`, у синтетики пусто.

## Путь новичка — `app/path`

Вехи адаптации без новых сообщений. Флаг `settings.path_enabled` (по умолчанию `True`). Синтетике (`is_synthetic`), не прошедшим знакомство и остановившим бота (`status == "stopped"`) — ничего. Тестовым новичкам (`is_test`, демо) — как настоящим, в том числе сообщение HR-группе.

- `service.path_state(emp) -> PathState` — вехи: «Знакомство» (засчитана сразу после знакомства) и опросы по порядку из `config/surveys.yaml`, у которых есть вопросы для площадки новичка (7 вех; 6, если на площадке нет опроса первого дня). Статус вехи: `done` — опрос завершён (`surveys[код].done_at`, ответы любые); `skipped` — не завершён, а путь ушёл дальше (`closed_at` или начат более поздний опрос); `ahead` — впереди. `PathState`: `milestones`, `passed` (только `done`), `current` («вы здесь»: последняя веха позади), `next`, `total`, `finished` (итоговый опрос пройден). Считается по `dialog_data`, поэтому машина времени и `surveys.reset` ничего не ломают.
- Два шага, чтобы «спасибо» не ждало картинку:
  - `service.line_for(emp, code) -> str | None` — строка «Веха N из 7 пройдена. Дальше: «…».» (одна и та же при любых ответах) сразу в сообщении, где веха пройдена;
  - `service.after_shown(repo, emp, code, *, sender, mid, text, atts, chat_id=None, with_card=True) -> вложение | None` — когда новичок уже видит это сообщение: карточка правкой того же `mid` (`card.attach`: отрисовка, загрузка, `PUT /messages`, повтор на `attachment.not.ready`, карточка вместо картинки шага), на последней вехе — один раз `notify_finished`.
- Вызывают `surveys.engine` (последний вид опроса: опрос `done`, текст не ждём, нет кнопки «Дальше»/«Готово»; строка и вставшая карточка кэшируются в `dialog_data["survey"]["path"]`, повторный показ берёт их оттуда) и `bot.handlers` (финал знакомства, `code="intro"`).
- `service.notify_finished` — в HR-группу «путь пройден, можно вручить мерч» (без ответов и обращений), отметка `dialog_data["path"]["hr_finished_at"]`; HR-группа не подключена — отметки нет. Новичку о подарке не пишем.
- `service.hello_line(state)` — «Пройдено N из 7 вех.» в приветствии `/start` (`HELLO_AGAIN`).
- `card.render_png(state, name) -> bytes` — HTML (`app/path/templates/path.html`, Exo 2, цвета Полипластика) → PNG 1080 × 1000 через общий Chromium `reports.render.screenshot` (браузер живёт в процессе, как у сводки). Любая ошибка (нет Chromium, загрузка, правка) — сообщение остаётся со строкой, без картинки.
- Загрузка картинки из памяти — `bot.uploads.attachment_bytes(data, name, kind, repo=, sender=)`, кэш токена по содержимому.
