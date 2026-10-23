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
- `X-Max-Init-Data` без символа «=» — строка, закодированная `encodeURIComponent` (кириллица в заголовке): бэкенд раскодирует её один раз.

### Раунд 3: карточка новичка

- `Employee.steps` — путь новичка по сценарию, по времени: `done` (отправленные шаги, один пункт на момент), `current` (шаг сегодняшнего дня, если есть), `upcoming` (до 5 ближайших). У синтетических новичков в статусе `active` отправок нет: пройденными считаются шаги плана, время которых наступило. `current_step` — сегодняшний шаг, иначе ближайший.
- `Employee.surveys`: пройден — опрос закончен (`done_at` в истории опросов; у синтетики — по ответам), `pending` — начат или отправлен и не закончен, `missed` — не закончен больше 3 дней или закрыт новым опросом. Опросные шаги — по номеру шага сценария (шаги 20, 21, 47 в xlsx имеют тип «сообщение»).
- `connected`: привязан к боту в «МАКС»; синтетические новички в статусе `active` считаются подключившимися, в статусе `waiting` — нет. Перематывать время на экране «Ещё» — только не синтетических.
- `intro.photo_url`: внешний https-адрес фото «МАКС» — как есть; файл или токен — `/api/employees/{id}/intro-photo?v=…&exp=…&sig=…` (относительный адрес, подпись на сутки: `<img>` не умеет слать заголовок initData). Без подписи эндпоинт принимает обычный `X-Max-Init-Data`. Токен «МАКС» без адреса скачать нельзя — `404`.
- Push руководителю о встрече и про доступы: «Открыть в Mini App» с `payload: "employee_<id>"` → `start_param`, Mini App открывает карточку новичка.
- `GET /api/topics` — по `questions_log` за 4 недели; синтетические вопросы создаёт `python -m seed.questions_seed` (часть `make seed`).

### Раунд 4: даты, план, режим руководителя

- `EmployeeShort.first_day` — первый рабочий день (`YYYY-MM-DD`): HR завёл выход на выходной или праздник — движок переносит его на ближайший рабочий, от него считается `day`. `start_date` — как завёл HR. В шапке карточки — `first_day`, если они расходятся — «перенесён с …».
- «Сегодня» новичка на фронте — `first_day + (day − 1)`: учитывает и перенос выхода, и машину времени. «Пора встретиться» у руководителя — `next_meeting.date` не позже этого дня (дата, а не номер встречи: встреча 3-го дня — на 3-й рабочий день).
- `Employee.plan_step` — `{at, sent}`: «План готов» только запоминает файл, новичку он уходит вместе с шагом «Адаптационный план» (`at`, ISO; `null` — шага плана для новичка нет) и только подключившемуся. `sent` — шаг уже ушёл.
- Файл плана (`POST /api/employees/{id}/plan`): `.pdf .doc .docx .xlsx .pptx .png .jpg .jpeg`, другие — `400`. Сценарий (`POST /api/scenario/import`) — только `.xlsx`.
- `Ticket.synthetic` — обращение синтетического новичка: в «МАКС» ему ничего не уходит, `delivered: false` после ответа — не сбой. `Ticket.is_personal` — переключателя «Добавить ответ в базу» нет; тост после ответа — по `added_to_kb` из ответа.
- «Посмотреть глазами руководителя» (`?role=manager`): `GET /api/employees?view=manager[&manager_id=]`, `GET /api/employees/{id}?view=manager`, `POST /api/employees/{id}/plan?view=manager`, `POST /api/meetings/{id}/done?view=manager`. Для HR и руководителя HR бэкенд отвечает как руководитель `manager_id` (по умолчанию — первый руководитель в базе): его новички, без опросов и обращений; чужой новичок — `404`. Настоящему руководителю параметр ничего не меняет. Так режим работает и внутри «МАКС», где `X-Dev-Role` не шлётся.
- `EmployeeShort.test` — тестовый новичок команды. Машина времени (`POST /api/demo/shift`) — только тестовым и синтетическим (живому — `403`), в «Кого перематываем» — только `test`. При `DEMO_ENABLED=false` демо-эндпоинты отвечают `404` «Демо-режим выключен»: экран «Ещё» прячет машину времени и кнопку сводки. Если `/api/me` отдаст `demo: bool`, фронт узнает об этом сразу, без первого запроса.
- Вход без initData — только с `localhost` (без заголовков прокси и туннеля, без чужого `Origin`): проверяйте фронт через `http://localhost:5173`.

- `POST /api/employees`: если дата выхода выпала на выходной или праздник, в ответе есть `warning` — текст для HR («31.10 нерабочий день. Первым рабочим днём новичка будет 02.11…»). Фронту показать его на листе с QR.

### Чаты команды

Групповые чаты «МАКС», куда добавлен Корсик. HR отмечает, кому из новичков нужен чат; в первый рабочий день бот сам добавляет туда новичка, а если «МАКС» не пускает (настройки приватности), присылает ему ссылки одним сообщением. Только HR (руководителю — `403`). Типы и запросы — `miniapp/src/api/chats.ts`, экран — `miniapp/src/screens/Chats.tsx`.

| Экран | Метод и путь | Тело запроса | Ответ |
|---|---|---|---|
| Список чатов | `GET /api/chats` | — | `TeamChat[]`: HR-группа первой, дальше активные по названию, чаты без бота — в конце |
| Правила чата | `PATCH /api/chats/{id}` | `TeamChatPatch`: только изменённые поля | `TeamChat` |

`TeamChat`:

```ts
{
  id: number               // chat_id «МАКС», у групп отрицательный
  title: string
  link: string | null      // ссылка-приглашение: её получит новичок, если добавить не вышло
  audience: 'none' | 'all' | 'sites'   // никому (по умолчанию, пока HR не настроил) | всем | площадкам из sites
  sites: string[]
  work_type: 'office' | 'production' | null   // null — и офису, и производству
  for_intro: boolean       // сюда бот публикует рассказ новичка о себе (с его согласия)
  is_hr_group: boolean     // сюда приходят обращения и сигналы; новичков сюда не добавляем
  active: boolean          // false — бота удалили из чата
  members_count: number | null
  added: number            // скольких новичков бот добавил
  links_sent: number       // скольким отправил ссылку вместо добавления
  created_at: string | null
}
```

`TeamChatPatch`: `{audience?, sites?, work_type?: 'office' | 'production' | 'all' | null, for_intro?, is_hr_group?}`. Поле не передано — не меняется; `work_type: null` или `"all"` — всем.

- `is_hr_group: true` — эта группа становится HR-группой (вместо прежней): правила сбрасываются (`audience: "none"`, `for_intro: false`). Если в группе висит сообщение с кнопкой «Сделать эту группу HR-группой», бот меняет его на месте, иначе коротко пишет в группу. `is_hr_group: false` у текущей — HR-группы больше нет.
- Ошибки: `400` — неизвестная площадка или правила у HR-группы («В HR-группу новичков не добавляем…»); `404` — чата нет в учёте; `409` — HR-группой нельзя сделать чат, из которого бота удалили; `422` — неверное значение `audience` / `work_type`.
- Чат появляется в списке, когда бота добавляют в группу. Пока HR-группа не выбрана, бот пишет в новой группе сообщение с кнопкой «Сделать эту группу HR-группой»: нажать может только подключённый к боту HR. Без прав админа бот не видит ссылку-приглашение и не может добавлять новичков: на экране — «Нет ссылки».
- Основной чат для представления новичка: с отметкой `for_intro`, иначе чат его площадки (`audience: "sites"`).

`Employee.chats` (карточка новичка) — куда бот добавил новичка:

```ts
{
  chat_id: number
  title: string
  status: 'added' | 'link' | 'failed' | 'pending' | 'intro'
  // добавлен | отправили ссылку | не получилось, ссылки нет | добавим в первый рабочий день | только опубликовали представление
  at: string | null
  reason: string | null    // почему не добавили: «не пустили настройки приватности»
  link: string | null
  intro: boolean           // в этом чате опубликован рассказ новичка о себе
}[]
```

У синтетических новичков `chats` пустой: бот с ними не работает. Подписи статусов — `EMPLOYEE_CHAT_STATUS` в `src/api/chats.ts`.

### Аналитика: руководитель HR и HR

Экран «Аналитика»: у руководителя HR (`role: "head"`) — первая вкладка и главный экран, у HR — вкладка после «Обращений». Руководителю подразделения — `403` «Аналитика доступна HR и руководителю HR», вкладки у него нет.

| Экран | Метод и путь | Ответ |
|---|---|---|
| Сводка | `GET /api/analytics[?site=GTP]` | `Analytics`: `funnel`, `mood.{sites, weeks, low_data}`, `clusters`, `risks.{total, high, medium, reasons}`, `kpi`, `sites`, `threshold`, `window` |
| В зоне риска | `GET /api/analytics/risks[?site=GTP][&level=high\|medium]` | `RiskItem[]`: сначала высокий уровень |

- `RiskItem`: `{employee_id, full_name, site, position, day, probation_days, stage, level: "high"|"medium", reasons: [{code, level, label, text, at}], connected, synthetic, card: "/api/employees/{id}", start_param: "employee_{id}"}`. Тап — карточка новичка (`GET /api/employees/{id}`).
- Причины — прозрачные правила, текст для человека: «Оценка упала с 9 до 5», «Нет ответа HR 2 дня». Коды: `red_flag`, `intent`, `score_drop`, `low_score`, `yellow_flag`, `missed_surveys`, `silent`, `not_connected`, `meeting_overdue`, `ticket_waiting`, `bot_stopped` (правила — `app/CONTRACTS.md`, раздел «Аналитика»).
- `funnel`: `[{stage: pre|week1|month1|month2|month3|done, label, count, risk, risk_high}]`, «Завершили» — за 3 месяца.
- `mood.weeks`: 8 календарных недель, `{start, end, label, respondents, shown, avg_score, mood, mood_total, mood_share, current?}`. `shown: false` — ответили меньше `threshold` (5), цифр нет. `mood.sites` — как строки еженедельной сводки за 4 недели.
- `clusters`: `[{topic, label, count, people, negative, prev, delta, trend: up|down|flat|new, trend_text, sites: [{code, name, count, prev, people}], quotes: [{text, site, site_name, mood, at}]}]` — за 4 недели и динамика к прошлым 4. Цитаты обезличены и только от тех, кто согласился на ИИ-разбор.
- Демо-данные: `mock.analytics(site)`, `mock.risks(site)` в `src/api/mocks.ts`.

