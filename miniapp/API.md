# API для Mini App

Контракт между Mini App (`miniapp/`) и бэкендом. Типы — в `src/api/types.ts`, эталонные ответы — в `src/api/mocks.ts`: бэкенд должен отдавать JSON той же формы.

## Общее

- Все запросы идут на `/api/...`. В dev Vite проксирует их на `API_PROXY_TARGET` (в compose — `http://app:8000`).
- В каждом запросе есть заголовок `X-Max-Init-Data: <window.WebApp.initData>`. Бэкенд проверяет HMAC-SHA256 (ключ от токена бота), `auth_date` не старше 24 часов, роль — по `user.id` → `staff.max_user_id`.
- Вне «МАКС» заголовок пустой. В dev бэкенд может пустить такой запрос как HR (флаг `DEV_ALLOW_NO_INIT_DATA`), в проде — `401`.
- Ответы — `application/json`. Если запрос упал, ответ не 2xx или пришёл не JSON, фронт берёт демо-данные и показывает плашку «демо-данные». `VITE_USE_MOCKS=1` включает демо-данные принудительно.
- Даты: `YYYY-MM-DD`. Время: ISO 8601 с часовым поясом.
- Площадки: `GTP`, `Luzhniki`, `GrPP`.

## Эндпоинты по экранам

| Экран | Метод и путь | Тело запроса | Ответ |
|---|---|---|---|
| Запуск, роль | `GET /api/me` | — | `Me` `{role: "hr" \| "manager" \| "head", name}` |
| Новички (HR) | `GET /api/employees` | — | `EmployeeShort[]` |
| Мои новички (руководитель) | `GET /api/employees?mine=1` | — | `EmployeeShort[]`, только новички этого руководителя |
| Карточка новичка | `GET /api/employees/{id}` | — | `Employee` |
| Завести новичка | `POST /api/employees` | `NewEmployeeInput` | `NewEmployeeResult` `{employee, invite_link, code}` |
| Обращения | `GET /api/tickets?status=open\|in_progress\|closed` | — | `TicketShort[]`: сначала красные флаги, потом новые |
| Бейдж вкладки «Обращения» | `GET /api/tickets?status=open` | — | длина массива |
| Экран обращения | `GET /api/tickets/{id}` | — | `Ticket` с `messages[]` |
| Ответ новичку | `POST /api/tickets/{id}/reply` | `ReplyInput` `{text, add_to_kb, kb_site}` | `Ticket` после ответа |
| Кнопка «Взять» | `POST /api/tickets/{id}/take` | `{}` | `Ticket` со `status: "in_progress"` и `assignee` |
| Кнопка «Закрыть» | `POST /api/tickets/{id}/close` | `{}` | `Ticket` со `status: "closed"` |
| База | `GET /api/kb/documents` | — | `KbDocument[]`, документы и ответы HR (`kind: "answer"`) |
| Загрузка документа | `POST /api/kb/documents` (multipart) | файл PDF / PPTX / XLSX | пока заглушка на фронте |
| Ещё → Частые темы | `GET /api/topics` | — | `Topic[]` `{topic, count, peak_days}` |
| Ещё → Демо | `POST /api/demo/shift` | `{to: "-2" \| "1" \| "3" \| "14" \| "60" \| "end"}` | `ShiftResult` `{employee_id, day, message}` |
| Ещё → Сформировать сводку | `POST /api/demo/report` | `{}` | `ReportResult` `{ok, message}` |
| Руководитель → «Провёл встречу» | `POST /api/meetings/{id}/done` | `{}` | `Employee` после отметки |

## Поведение, которое ждёт фронт

- `EmployeeShort.day` — день адаптации с 1. `0` и меньше — до выхода. `probation_days` — длина испытательного срока в днях.
- `EmployeeShort.next_meeting` — ближайшая непроведённая встреча с руководителем (3-й, 30-й, 60-й день и за 5 дней до конца испытательного).
- `POST /api/tickets/{id}/reply` отправляет текст новичку в чат «МАКС». Если обращение было `open`, переводит его в `in_progress` и назначает текущего HR.
- `add_to_kb: true` создаёт ответ HR в базе со статусом `draft` для площадки `kb_site` (`"all"` — все площадки). Бот начинает использовать ответ после подтверждения.
- **Руководитель** не получает ответы опросов и обращения. Для роли `manager` поля `pending_surveys`, `open_tickets`, `red_flag`, `surveys`, `tickets` приходят пустыми или нулевыми, `GET /api/tickets*` отвечает `403`.
- `invite_link` — диплинк на бота в «МАКС» с кодом привязки, например `https://max.ru/<bot>?start=<code>`. QR строится на фронте.
- Все демо-сущности помечены `synthetic: true`.

## Раунд 2: новые эндпоинты

Владелец контракта — бэкенд (`app/api`). Фронт реализует то, что здесь описано; если нужно ещё что-то — фронт делает экран на демо-данных и пишет запрос в отчёте.

| Экран | Метод и путь | Тело запроса | Ответ |
|---|---|---|---|
| Новичок: подключён ли, ссылка | `GET /api/employees/{id}` | — | `Employee` с полями `connected: bool`, `invite_link`, `intro: {text, photo_url, at} \| null`, `gates: {recruiter_go: bool, plan_ready: bool}` |
| Отмашка «представить коллегам» | `POST /api/employees/{id}/gate` | `{gate: "recruiter_go"}` | `Employee` |
| «План готов» | `POST /api/employees/{id}/plan` (multipart, поле `file`) | файл плана | `Employee` |
| Справочники формы | `GET /api/sites`, `GET /api/staff?role=manager\|recruiter` | — | `[{code, name, tz}]`, `[{id, name, role}]` |
| Новая площадка из xlsx | `POST /api/sites` | `{code, name, tz}` | `{code, name, tz}` |
| Загрузка документа | `POST /api/kb/documents` (multipart: `file`, `title`, `is_kt`, `site_codes` через запятую) | PDF / PPTX | `KbDocument` с `chunks: number` |
| Подтвердить ответ HR | `POST /api/kb/answers/{id}/confirm` | `{}` | `KbDocument` (`kind: "answer"`, `status: "confirmed"`) |
| Загрузка сценария | `POST /api/scenario/import` (multipart, поле `file`) | xlsx | `{steps, by_site: {code: n}, surveys, attachments, new_sites: [code], warnings: [string]}` |
| Демо: кто перематывается | `POST /api/demo/shift` | `{to, employee_id?}` | как раньше; без `employee_id` — первый тестовый новичок |

Ошибки — `{"detail": "текст для человека"}` с кодом 4xx. Если модуль ещё не готов — `501` с `detail`.

### Дополнения бэкенда (раунд 2, реализовано)

Новые эндпоинты:

| Экран | Метод и путь | Тело запроса | Ответ |
|---|---|---|---|
| Подтвердить черновик документа | `POST /api/kb/documents/{id}/confirm` | `{}` | `KbDocument` со `status: "confirmed"` (например, статья «Доступ к корпоративному порталу») |
| Подтвердить ответ HR с правкой | `POST /api/kb/answers/{id}/confirm` | `{question?, answer?}` | можно поправить текст перед подтверждением; `{}` — как есть |
| Демо: вернуть время | `POST /api/demo/shift` | `{to: "now", employee_id?}` | `ShiftResult`, сдвиг времени сброшен |

Новые поля в ответах (фронт может их не читать):

- `Me.id` — id сотрудника.
- `Employee.status` — `waiting | active | stopped | finished`. `intro_text` = `intro.text`.
- `SurveyItem.score` бывает `null` и у пройденного опроса, если в нём нет вопроса-шкалы.
- `Ticket.is_personal` — личный вопрос; такой ответ в базу не добавляется.
- Ответ `POST /api/tickets/{id}/reply` — `Ticket` плюс `delivered: bool` (ушло ли новичку в «МАКС»: `false`, если новичок не подключён или бот выключен; ответ всё равно в ленте) и `added_to_kb: bool`.
- `KbDocument.chunks` (= `fragments`), `doc_type` (как в БД: `pdf | pptx | md | scenario | article`), `sites: string[]`. У ответов HR ещё `question`, `answer`, `ticket_id`.
- `GET /api/staff` → `[{id, name, role, site, connected}]`. `role=recruiter` отдаёт рекрутеров и HR, `role=manager` — руководителей.

Поведение:

- Статус обращения: в БД `new`, в API — `open`.
- `TicketShort.category` — из списка `TicketCategory`; тема флага, которую не удалось сопоставить, приходит как есть, подпись — в `category_label`. Для `question` подпись — тема вопроса, если бот её определил («Зарплата»).
- `due_at`: красный флаг — час с момента обращения, остальное — сутки. После первого ответа HR и у закрытых — `null`.
- `Ticket.messages`: пара сообщений до обращения (контекст: что ответил бот) и всё, что попало в обращение. Если цитаты новичка нет в журнале (обращение из опроса или флага), она первым сообщением с отрицательным `id`. Строка «Источник: …» бота вынесена в `source`.
- `POST /api/tickets/{id}/reply`: ответ уходит новичку с подписью «Имя (HR)»; 30 минут после этого сообщения новичка попадают в то же обращение. Закрытое обращение — `409`.
- «Закрыть» новичку ничего не пишет: если HR уже ответил, лишнее сообщение не нужно.
- `KbDocument.kind` для документов из `docs/*.md` — по формату оригинала (ПВТР — `pdf`, политики — `pptx`), справочные статьи из сценария — `xlsx`. Если в БД есть шаги сценария, в списке есть строка `id: 0, kind: "xlsx"` «Сценарий адаптации».
- `POST /api/employees`: `invite_link` = `https://max.ru/<MAX_BOT_NAME>?start=<code>`; встречи с руководителем создаются сразу. `probation_days` считается с дня выхода включительно.
- Руководитель: `GET /api/employees` — только свои (с `mine=1` и без); чужой новичок — `404`; `/api/tickets*`, `/api/kb/*`, `/api/topics`, `/api/demo/*`, `POST /api/sites`, `POST /api/employees`, `POST /api/employees/{id}/gate` — `403`. «План готов» и «Провёл встречу» — можно, только по своим новичкам.
- Уведомление в HR-группе о новом обращении: кнопка «Открыть в Mini App» (`open_app` с `payload: "ticket_<id>"` — придёт в Mini App как `start_param`, можно сразу открыть обращение) и «Взять себе».
- Файлы загрузок — `data/uploads/{kb,plans,scenario}/`. Лимит — 50 МБ.
- Коды ошибок: `401` — нет или неверный `initData` (в т. ч. старше 24 часов), `403` — роль, `404`, `409` — обращение закрыто, `422` — неверное тело, `501` — модуль ещё не готов.
- Разработка: без `initData` при `DEV_ALLOW_NO_INIT_DATA=true` запрос идёт как первый HR из БД; заголовок `X-Dev-Role: manager | head | hr` — как первый сотрудник этой роли.
