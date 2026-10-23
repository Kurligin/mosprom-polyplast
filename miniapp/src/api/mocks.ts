// Демо-данные. Все люди и обращения синтетические.
import type {
  Analytics, Cluster, Contact, Employee, EmployeeShort, GateName, KbDocument, Me, Meeting, Message, MoodWeek,
  NewEmployeeInput, NewEmployeeResult, ReplyInput, ReportResult, RiskItem, RiskReason, Role, ScenarioReport,
  ShiftResult, ShiftTo, Site, SiteCode, StaffMember, StaffRole, StageCode, Step, SurveyItem, Ticket, TicketShort,
  TicketStatus, Topic, UploadDocumentInput, WorkType,
} from './types'

const sites: Site[] = [
  { code: 'GTP', name: 'GTP', tz: 'Europe/Moscow' },
  { code: 'Luzhniki', name: 'Luzhniki', tz: 'Europe/Moscow' },
  { code: 'GrPP', name: 'GrPP', tz: 'Europe/Moscow' },
]

const today = new Date()
today.setHours(9, 0, 0, 0)
const iso = (d: Date) => d.toISOString().slice(0, 10)
const addDays = (d: Date, n: number) => {
  const r = new Date(d)
  r.setDate(r.getDate() + n)
  return r
}
/** Первый рабочий день: выход в выходной переносится на понедельник (праздники демо не учитывает) */
const firstWorkday = (d: Date) => addDays(d, d.getDay() === 6 ? 2 : d.getDay() === 0 ? 1 : 0)
const hoursAgo = (h: number) => new Date(Date.now() - h * 3600_000).toISOString()
const hoursAhead = (h: number) => new Date(Date.now() + h * 3600_000).toISOString()

const MANAGERS: Contact[] = [
  { id: 101, name: 'Соколов Андрей', role: 'Руководитель, производство GTP', phone: '+7 900 000-01-01' },
  { id: 102, name: 'Белова Ирина', role: 'Руководитель, офис Luzhniki', phone: '+7 900 000-01-02' },
  { id: 103, name: 'Гусев Павел', role: 'Руководитель, производство GrPP', phone: '+7 900 000-01-03' },
]
const RECRUITERS: Contact[] = [
  { id: 201, name: 'Миронова Ольга', role: 'Рекрутер', phone: '+7 900 000-02-01' },
  { id: 202, name: 'Ковалёв Денис', role: 'Рекрутер', phone: '+7 900 000-02-02' },
]

type Seed = [name: string, position: string, site: SiteCode, work: WorkType, day: number, probation: number, flags: Partial<EmployeeShort> & { go?: boolean; plan?: boolean }]

const SEED: Seed[] = [
  ['Петров Илья', 'Оператор экструдера', 'GTP', 'production', 3, 90, { pending_surveys: 1, go: true }],
  ['Захарова Мария', 'Инженер-технолог', 'GTP', 'production', 12, 90, { go: true, plan: true }],
  ['Орлов Никита', 'Наладчик оборудования', 'GTP', 'production', 34, 90, { go: true, plan: true }],
  ['Кузнецова Анна', 'Лаборант ОТК', 'GTP', 'production', 61, 90, { pending_surveys: 1, go: true, plan: true }],
  ['Фёдоров Артём', 'Кладовщик', 'GTP', 'production', 1, 90, { connected: false }],
  ['Смирнова Елена', 'Бухгалтер', 'Luzhniki', 'office', 5, 90, { go: true }],
  ['Волков Дмитрий', 'Менеджер по закупкам', 'Luzhniki', 'office', 18, 90, { go: true, plan: true }],
  ['Лебедева Ксения', 'HR-специалист', 'Luzhniki', 'office', 2, 90, { pending_surveys: 1 }],
  ['Никитин Роман', 'Аналитик продаж', 'Luzhniki', 'office', 47, 180, { go: true, plan: true }],
  ['Морозова Дарья', 'Юрист', 'Luzhniki', 'office', 85, 90, { go: true, plan: true }],
  ['Алексеев Сергей', 'Оператор линии', 'GrPP', 'production', 7, 90, { go: true }],
  ['Тихонова Вера', 'Мастер смены', 'GrPP', 'production', 26, 90, { pending_surveys: 2, go: true, plan: true }],
  ['Егоров Максим', 'Слесарь-ремонтник', 'GrPP', 'production', 4, 90, {}],
  ['Павлова Светлана', 'Специалист по ОТ', 'GrPP', 'production', 58, 90, { go: true, plan: true }],
  ['Романов Кирилл', 'Электрик', 'GrPP', 'production', 0, 90, { connected: false }],
]

const managerFor = (site: SiteCode) => (site === 'GTP' ? MANAGERS[0] : site === 'Luzhniki' ? MANAGERS[1] : MANAGERS[2])

const MEETING_DAYS = (probation: number) => [
  { day: 3, title: 'Встреча на 3-й день' },
  { day: 30, title: 'Встреча на 30-й день' },
  { day: 60, title: 'Встреча на 60-й день' },
  { day: probation - 5, title: 'Итоговая встреча' },
]

function meetingsFor(id: number, start: Date, day: number, probation: number): Meeting[] {
  return MEETING_DAYS(probation).map((m, i) => ({
    id: id * 10 + i,
    day: m.day,
    title: m.title,
    date: iso(addDays(start, m.day - 1)),
    done: m.day < day,
  }))
}

const STEP_TEMPLATE = (work: WorkType): { day: number; title: string }[] => [
  { day: 1, title: 'Знакомство с Корсиком и пропуск' },
  { day: 1, title: work === 'production' ? 'Вводный инструктаж по охране труда' : 'Рабочее место и доступы' },
  { day: 2, title: 'Рассказать о себе коллегам' },
  { day: 3, title: 'Опрос «Первые дни»' },
  { day: 7, title: 'Знакомство с ценностями компании' },
  { day: 14, title: 'Опрос «Две недели»' },
  { day: 30, title: 'Встреча с руководителем, 30 дней' },
  { day: 60, title: 'Опрос «Два месяца»' },
]

function stepsFor(start: Date, day: number, work: WorkType): Step[] {
  let currentSet = false
  return STEP_TEMPLATE(work).map((s) => {
    let status: Step['status'] = 'upcoming'
    if (s.day < day) status = 'done'
    else if (!currentSet) {
      status = 'current'
      currentSet = true
    }
    return { ...s, date: iso(addDays(start, s.day - 1)), status }
  })
}

function surveysFor(id: number, day: number, pending: number): SurveyItem[] {
  const all = [
    { day: 3, title: 'Первые дни' },
    { day: 14, title: 'Две недели' },
    { day: 30, title: 'Месяц в компании' },
    { day: 60, title: 'Два месяца' },
  ].filter((s) => s.day <= Math.max(day, 3))
  return all.map((s, i) => {
    const isPending = i >= all.length - pending
    return {
      id: id * 10 + i,
      title: s.title,
      day: s.day,
      status: s.day > day ? 'pending' : isPending ? 'pending' : 'done',
      score: s.day > day || isPending ? null : 7 + ((id + i) % 4),
    }
  })
}

const newCode = () => Math.random().toString(36).slice(2, 10).toUpperCase()
const inviteLink = (code: string) => `https://max.ru/korsik_bot?start=${code}`

const employees: Employee[] = SEED.map(([full_name, position, site, work_type, day, probation_days, flags], i) => {
  const id = i + 1
  const start = addDays(today, -(day - 1))
  const meetings = meetingsFor(id, start, day, probation_days)
  const steps = stepsFor(start, day, work_type)
  const pending = flags.pending_surveys ?? 0
  const first = full_name.split(' ')[1]
  const female = full_name.split(' ')[0].endsWith('а')
  return {
    id,
    full_name,
    position,
    site,
    work_type,
    start_date: iso(start),
    first_day: iso(start),
    day,
    probation_days,
    current_step: steps.find((s) => s.status === 'current')?.title ?? null,
    pending_surveys: pending,
    open_tickets: 0,
    red_flag: false,
    connected: flags.connected ?? true,
    next_meeting: meetings.find((m) => !m.done) ?? null,
    synthetic: true,
    test: id === 1, // первый — «тестовый новичок» команды: его перематывает машина времени
    manager: managerFor(site),
    recruiter: RECRUITERS[i % 2],
    invite_link: inviteLink(`DEMO${id}`),
    intro:
      flags.go && day >= 3
        ? {
            text: `Всем привет! Я ${first}, ${position.toLowerCase()}. Рад${female ? 'а' : ''} присоединиться к команде ${site}. До этого пять лет работал${female ? 'а' : ''} на производстве упаковки, люблю разбираться, как всё устроено. По выходным катаюсь на велосипеде.`,
            photo_url: null,
            at: addDays(start, 1).toISOString(),
          }
        : null,
    gates: { recruiter_go: !!flags.go, plan_ready: !!flags.plan },
    plan_step: { at: addDays(start, 2).toISOString(), sent: !!flags.plan && day >= 3 },
    steps,
    surveys: surveysFor(id, day, pending),
    tickets: [],
    meetings,
  }
})

let ticketSeq = 1
let messageSeq = 1
const msg = (author: Message['author'], author_name: string, text: string, h: number, source: string | null = null): Message => ({
  id: messageSeq++,
  author,
  author_name,
  text,
  created_at: hoursAgo(h),
  source,
})

function makeTicket(
  employeeId: number,
  category: Ticket['category'],
  category_label: string,
  red_flag: boolean,
  status: TicketStatus,
  createdH: number,
  dueH: number | null,
  messages: Message[],
  assignee: string | null = null,
): Ticket {
  const e = employees.find((x) => x.id === employeeId)!
  const first = messages.find((m) => m.author === 'employee')?.text ?? ''
  return {
    id: ticketSeq++,
    employee_id: e.id,
    employee_name: e.full_name,
    site: e.site,
    day: e.day,
    category,
    category_label,
    red_flag,
    status,
    preview: first.split('\n')[0],
    created_at: hoursAgo(createdH),
    due_at: dueH === null ? null : dueH >= 0 ? hoursAhead(dueH) : hoursAgo(-dueH),
    assignee,
    messages,
  }
}

const tickets: Ticket[] = [
  makeTicket(2, 'safety', 'Охрана труда', true, 'open', 1, 1, [
    msg('bot', 'Корсик', 'Как прошла первая неделя на участке? Оцените от 1 до 10 и, если хотите, напишите пару слов.', 2),
    msg('employee', 'Захарова Мария', 'На линии 3 не работает аварийная кнопка остановки, мастер сказал пока так работать. Мне страшно подходить к экструдеру.', 1),
    msg('bot', 'Корсик', 'Спасибо, что написали. Это важно, я передал сообщение HR, его увидит только HR. Если опасно прямо сейчас, остановите работу и сообщите мастеру смены или по телефону охраны труда.', 1),
  ]),
  makeTicket(6, 'question', 'Отпуск', false, 'open', 5, 19, [
    msg('employee', 'Смирнова Елена', 'Можно ли взять отпуск за свой счёт на 3 дня в ноябре, если я работаю меньше месяца?', 5),
    msg('bot', 'Корсик', 'В правилах есть пункт об отпуске без сохранения зарплаты:\n\n«По семейным обстоятельствам и другим уважительным причинам работнику по его письменному заявлению может быть предоставлен отпуск без сохранения заработной платы…»', 5, 'ПВТР, п. 6.9, стр. 18'),
    msg('employee', 'Смирнова Елена', 'А кому писать заявление и за сколько дней?', 4),
    msg('bot', 'Корсик', 'Точного ответа в документах не нашёл. Передал вопрос HR, ответ придёт сюда.', 4),
  ]),
  makeTicket(11, 'unknown', 'Не знаю → HR', false, 'open', 8, -2, [
    msg('employee', 'Алексеев Сергей', 'Где получить вторую пару спецобуви? Первая промокла, а смена завтра в 7.', 8),
    msg('bot', 'Корсик', 'Не нашёл ответа в документах. Передал вопрос HR, ответ придёт сюда. Можете задать другой вопрос.', 8),
  ]),
  makeTicket(9, 'contact_request', 'Просьба связаться', false, 'in_progress', 26, 22, [
    msg('employee', 'Никитин Роман', 'Свяжитесь со мной, пожалуйста, хочу обсудить условия перевода на гибридный график.', 26),
    msg('bot', 'Корсик', 'Передал HR вашу просьбу связаться. Обычно отвечают в течение рабочего дня.', 26),
    msg('hr', 'Лебедева Ксения', 'Роман, добрый день! Позвоню вам завтра до 12:00, удобно?', 3),
  ], 'Лебедева К.'),
  makeTicket(12, 'intent_to_leave', 'Намерение уйти', true, 'in_progress', 30, 2, [
    msg('bot', 'Корсик', 'Опрос «Месяц в компании». Насколько вы уверены, что останетесь с нами после испытательного срока?', 31),
    msg('employee', 'Тихонова Вера', '3 из 10. Ночные смены по графику не так, как обсуждали на собеседовании. Думаю уходить.', 30),
  ], 'Миронова О.'),
  makeTicket(3, 'question', 'Зарплата', false, 'closed', 72, null, [
    msg('employee', 'Орлов Никита', 'Когда приходит аванс?', 72),
    msg('bot', 'Корсик', '«Заработная плата выплачивается не реже чем каждые полмесяца…»', 72, 'ПВТР, п. 7.3, стр. 21'),
    msg('employee', 'Орлов Никита', 'А какие именно числа?', 71),
    msg('hr', 'Лебедева Ксения', 'Аванс 25-го, зарплата 10-го числа. Если день выпадает на выходной, деньги придут накануне.', 70),
  ], 'Лебедева К.'),
  makeTicket(7, 'question', 'Пропуск', false, 'closed', 120, null, [
    msg('employee', 'Волков Дмитрий', 'Пропуск перестал открывать турникет на 2-м этаже.', 120),
    msg('hr', 'Ковалёв Денис', 'Передали в АХО, доступ восстановлен.', 118),
  ], 'Ковалёв Д.'),
]

function recount() {
  for (const e of employees) {
    const own = tickets.filter((t) => t.employee_id === e.id)
    e.open_tickets = own.filter((t) => t.status !== 'closed').length
    e.red_flag = own.some((t) => t.red_flag && t.status !== 'closed')
    e.tickets = own.map(shortTicket)
  }
}

function shortTicket(t: Ticket): TicketShort {
  const { messages: _m, assignee: _a, ...rest } = t
  void _m
  void _a
  return rest
}

function shortEmployee(e: Employee): EmployeeShort {
  const { manager: _1, recruiter: _2, intro: _3, steps: _4, surveys: _5, tickets: _6, meetings: _7, gates: _8, invite_link: _9, plan_step: _10, ...rest } = e
  void _1; void _2; void _3; void _4; void _5; void _6; void _7; void _8; void _9; void _10
  return rest
}

recount()

let docSeq = 100
const documents: KbDocument[] = [
  { id: 1, title: 'Правила внутреннего трудового распорядка П 3.11-07', kind: 'pdf', trade_secret: false, status: 'confirmed', version_date: '2025-03-12', fragments: 142, site: null },
  { id: 2, title: 'Положение о коммерческой тайне', kind: 'pptx', trade_secret: true, status: 'confirmed', version_date: '2025-01-20', fragments: 38, site: null },
  { id: 3, title: 'Антикоррупционная политика', kind: 'pptx', trade_secret: false, status: 'confirmed', version_date: '2024-11-05', fragments: 27, site: null },
  { id: 4, title: 'Политика в области качества и экологии', kind: 'pptx', trade_secret: false, status: 'confirmed', version_date: '2024-09-18', fragments: 19, site: null },
  { id: 5, title: 'Сценарий адаптации «Корсик», 68 шагов', kind: 'xlsx', trade_secret: false, status: 'confirmed', version_date: '2025-09-15', fragments: 68, site: null },
  { id: 9, title: 'Доступ к корпоративному порталу', kind: 'pdf', trade_secret: false, status: 'draft', version_date: iso(today), fragments: 1, site: null },
  { id: 6, title: 'Когда приходят аванс и зарплата', kind: 'answer', trade_secret: false, status: 'confirmed', version_date: iso(addDays(today, -3)), fragments: 1, site: 'GTP', text: 'Аванс 25-го, зарплата 10-го числа. Если день выпадает на выходной, деньги придут накануне.' },
  { id: 7, title: 'Где получить вторую пару спецобуви', kind: 'answer', trade_secret: false, status: 'draft', version_date: iso(today), fragments: 1, site: 'GrPP', text: 'Вторую пару выдают на складе СИЗ у проходной №2 с 8:00 до 16:00. Возьмите пропуск, заявка не нужна.' },
  { id: 8, title: 'Можно ли парковаться у главного входа', kind: 'answer', trade_secret: false, status: 'draft', version_date: iso(addDays(today, -1)), fragments: 1, site: null, text: 'У главного входа парковка только для гостей. Сотрудники ставят машину на парковке П2 за складом, пропуск тот же.' },
]

const topics: Topic[] = [
  { topic: 'Пропуск и проход', count: 34, peak_days: 'дни 1–3' },
  { topic: 'Зарплата и аванс', count: 27, peak_days: 'дни 10–20' },
  { topic: 'Спецодежда и СИЗ', count: 21, peak_days: 'дни 1–7' },
  { topic: 'Отпуск', count: 15, peak_days: 'дни 30–60' },
  { topic: 'Обучение и инструктажи', count: 12, peak_days: 'дни 1–14' },
  { topic: 'Питание и столовая', count: 9, peak_days: 'дни 1–5' },
]

/*
 * Худший реалистичный случай (скилл break-ui): только в dev, адрес ?mocks=worst.
 * Длинные ФИО с отчеством и двойной фамилией, двухбуквенное имя, длинные названия площадки и документа,
 * все чипы сразу, срок больше испытательного, ровно 1 фрагмент, битая ссылка на фото, нет руководителя.
 */
if (import.meta.env.DEV && new URLSearchParams(location.search).get('mocks') === 'worst') {
  sites[2].name = 'Группа ПОЛИПЛАСТИК, площадка Наро-Фоминск (GrPP)'
  const [a, b, c, d] = employees
  a.full_name = 'Константинопольская-Римская Александра Владиславовна'
  a.position = 'Ведущий инженер-технолог по переработке полимерных материалов'
  a.pending_surveys = 3
  a.connected = false
  a.intro = {
    text: 'Всем привет! '.concat('Я отвечаю за качество гранулята и люблю длинные сообщения. '.repeat(8)),
    photo_url: 'https://example.invalid/photo-that-does-not-exist.jpg',
    at: new Date().toISOString(),
  }
  a.manager = null
  a.invite_link = 'https://max.ru/korsik_polyplastic_adaptation_bot?start=AbCdEfGhIjKlMnOpQrStUvWxYz0123456789'
  b.full_name = 'Ли Ю'
  b.position = ''
  c.day = 95
  c.probation_days = 90
  d.day = -14
  d.current_step = null
  tickets[0].category_label = 'Намерение уйти после испытательного срока'
  tickets[0].employee_name = a.full_name
  tickets[0].messages[1].text = 'Здравствуйте! '.concat('У нас на участке постоянно меняют график, и я не понимаю, кому писать. '.repeat(5))
  tickets[0].preview = tickets[0].messages[1].text
  documents[0].title = 'Положение о порядке предоставления ежегодных оплачиваемых отпусков работникам ООО «Группа ПОЛИПЛАСТИК» и дочерних обществ, редакция 2026'
  documents[1].fragments = 1
  documents[6].title = 'Можно ли получить вторую пару спецобуви, если первая промокла, а смена начинается завтра в семь утра?'
  documents[6].text = 'Да. '.concat('Вторую пару выдают на складе СИЗ у проходной №2 с 8:00 до 16:00, возьмите пропуск. '.repeat(4))
  topics[0].topic = 'Пропуск, проход через турникеты и парковка для сотрудников'
  topics[0].count = 1284
}

/* ---------- Аналитика: демо-данные, все люди синтетические ---------- */

const stageOf = (day: number, total: number): StageCode =>
  day > total ? 'done' : day <= 0 ? 'pre' : day <= 7 ? 'week1' : day <= 30 ? 'month1' : day <= 60 ? 'month2' : 'month3'

const reason = (code: string, level: RiskItem['level'], label: string, text: string, h: number | null = null): RiskReason =>
  ({ code, level, label, text, at: h === null ? null : hoursAgo(h) })

const RISKS: [id: number, level: RiskItem['level'], reasons: RiskReason[]][] = [
  [4, 'high', [reason('score_drop', 'high', 'падение оценок', 'Оценка упала с 9 до 5', 20),
    reason('intent', 'medium', 'намерение уйти', 'Сомневается, что останется после испытательного', 20)]],
  [12, 'high', [reason('intent', 'high', 'намерение уйти', 'Думает об уходе: сообщение 3 дня назад', 72),
    reason('meeting_overdue', 'medium', 'просрочены встречи', 'Встреча на 30-й день: просрочена на 5 рабочих дней'),
    reason('ticket_waiting', 'medium', 'нет ответа HR', 'Нет ответа HR 2 дня', 50)]],
  [1, 'high', [reason('red_flag', 'high', 'красные сигналы', 'Красный сигнал: безопасность, вчера', 26)]],
  [5, 'medium', [reason('not_connected', 'medium', 'не подключились', 'Выход сегодня, а в боте ещё нет')]],
  [9, 'medium', [reason('missed_surveys', 'medium', 'пропуск опросов', '2 опроса подряд без ответа'),
    reason('silent', 'medium', 'молчат', 'Молчит 12 дней, нет ответа на «Два месяца»', 288)]],
  [7, 'medium', [reason('yellow_flag', 'medium', 'жёлтые сигналы', 'Жёлтый сигнал: нагрузка', 40)]],
  [13, 'medium', [reason('low_score', 'medium', 'низкие оценки', 'Оценка 5,1 из 10 в опросе «Чек через 2 недели»', 90)]],
]

function mockRisks(site?: SiteCode | null): RiskItem[] {
  return RISKS.flatMap(([id, level, reasons]) => {
    const e = employees.find((x) => x.id === id)
    if (!e || (site && e.site !== site)) return []
    return [{
      employee_id: e.id, full_name: e.full_name, site: e.site, position: e.position, day: e.day,
      probation_days: e.probation_days, stage: stageOf(e.day, e.probation_days), level, reasons,
      connected: e.connected, synthetic: true, card: `/api/employees/${e.id}`, start_param: `employee_${e.id}`,
    }]
  })
}

const CLUSTERS: Cluster[] = [
  { topic: 'доступы', label: 'Доступы', count: 9, people: 7, negative: 6, prev: 4, delta: 5, trend: 'up',
    trend_text: 'в 2 раза больше, чем месяц назад',
    sites: [{ code: 'GTP', count: 6, prev: 2, people: 5 }, { code: 'other', name: 'Другие площадки', count: 3, prev: 2, people: 2 }],
    quotes: [
      { text: 'Ноутбук выдали, а учётки нет. Айтишники говорят, заявку должен был подать руководитель', mood: 'negative', at: hoursAgo(30) },
      { text: 'Пропуск сделали только к обеду, до этого ходил по журналу', mood: 'negative', at: hoursAgo(80) },
    ], quotes_reason: null },
  { topic: 'понимание задач', label: 'Понимание задач', count: 7, people: 6, negative: 4, prev: 3, delta: 4, trend: 'up',
    trend_text: 'в 2 раза больше, чем месяц назад', sites: [],
    quotes: [{ text: 'Каждый день ставят на новую линию, никто не объясняет почему', mood: 'negative', at: hoursAgo(50) }],
    quotes_reason: null },
  { topic: 'наставник', label: 'Наставник', count: 4, people: 3, negative: 3, prev: 0, delta: 4, trend: 'new',
    trend_text: 'новая тема', sites: [], quotes: [], quotes_reason: 'few' },
  { topic: 'нагрузка', label: 'Нагрузка', count: 5, people: 4, negative: 3, prev: 6, delta: -1, trend: 'flat',
    trend_text: 'на 1 меньше, чем месяц назад', sites: [], quotes: [], quotes_reason: 'few' },
  { topic: 'быт и транспорт', label: 'Быт и транспорт', count: 2, people: 2, negative: 1, prev: 5, delta: -3, trend: 'down',
    trend_text: 'на 3 меньше, чем месяц назад', sites: [], quotes: [], quotes_reason: 'few' },
]

function mockWeeks(): MoodWeek[] {
  const monday = addDays(today, -((today.getDay() + 6) % 7))
  const shares: [number, number, number, number][] = [ // позитив, нейтр., негатив, ответили
    [0, 0, 0, 3], [12, 4, 3, 6], [10, 5, 6, 7], [9, 4, 9, 8], [11, 5, 4, 9], [13, 3, 3, 7], [8, 6, 9, 8], [6, 2, 3, 5],
  ]
  return shares.map(([pos, neu, neg, people], i) => {
    const start = addDays(monday, (i - 7) * 7)
    const end = addDays(start, 6)
    const total = pos + neu + neg
    const shown = people >= 5
    return {
      start: iso(start), end: iso(end), label: `${start.getDate()}–${end.getDate()}`, respondents: people, shown,
      avg_score: null, mood: { positive: pos, neutral: neu, negative: neg }, mood_total: shown ? total : 0,
      mood_share: shown && total
        ? { positive: pos / total, neutral: neu / total, negative: neg / total }
        : { positive: null, neutral: null, negative: null },
      current: i === 7,
    }
  })
}

function mockAnalytics(site?: SiteCode | null): Analytics {
  const list = employees.filter((e) => !site || e.site === site)
  const risks = mockRisks(site)
  const stages: [StageCode, string][] = [['pre', 'До выхода'], ['week1', '1-я неделя'], ['month1', '1-й месяц'],
    ['month2', '2-й месяц'], ['month3', '3-й месяц'], ['done', 'Завершили']]
  const funnel = stages.map(([stage, label]) => {
    const here = list.filter((e) => stageOf(e.day, e.probation_days) === stage)
    const r = risks.filter((x) => x.stage === stage)
    return { stage, label, count: here.length + (stage === 'done' ? 3 : 0), risk: r.length,
             risk_high: r.filter((x) => x.level === 'high').length }
  })
  const reasons = new Map<string, { code: string; label: string; count: number }>()
  risks.forEach((r) => r.reasons.forEach((x) => {
    const c = reasons.get(x.code) ?? { code: x.code, label: x.label, count: 0 }
    c.count += 1
    reasons.set(x.code, c)
  }))
  const siteRows = sites.filter((s) => !site || s.code === site).map((s, i) => ({
    code: s.code, name: s.name, newbies: list.filter((e) => e.site === s.code).length, respondents: 5 + i,
    avg_score: [6.8, 7.9, 7.4][i] ?? null, mood: { positive: 14 - i * 3, neutral: 5, negative: 7 - i * 2 },
    mood_total: 26 - i * 5, low_data: false,
    mood_share: { positive: (14 - i * 3) / (26 - i * 5), neutral: 5 / (26 - i * 5), negative: (7 - i * 2) / (26 - i * 5) },
  }))
  return {
    generated_at: new Date().toISOString(), site: site ?? null, weeks: 4,
    window: { start: iso(addDays(today, -27)), end: iso(today), label: 'за 4 недели' }, threshold: 5,
    kpi: { newbies: list.filter((e) => e.day <= e.probation_days).length, newbies_week: 2, respondents: 14, avg_score: 7.4,
           signals_open: 5, signals_red_open: 1, risks: risks.length, risks_high: risks.filter((r) => r.level === 'high').length },
    funnel,
    mood: { sites: siteRows, low_data: [], weeks: mockWeeks() },
    // по одной площадке ответивших в демо меньше порога: темы не показываем, как на сервере
    clusters: site ? [] : CLUSTERS,
    clusters_low_data: !!site,
    risks: { total: risks.length, high: risks.filter((r) => r.level === 'high').length,
             medium: risks.filter((r) => r.level === 'medium').length, reasons: [...reasons.values()].sort((a, b) => b.count - a.count) },
    sites: sites.map((s) => ({ code: s.code, name: s.name })),
    synthetic: true,
  }
}

const notFound = (what: string) => {
  throw new Error(`${what} не найден в демо-данных`)
}

export const mock = {
  me(roleParam: string | null): Me {
    const role: Role = roleParam === 'manager' ? 'manager' : roleParam === 'head' ? 'head' : 'hr'
    return role === 'manager' ? { role, name: 'Соколов Андрей' } : { role, name: 'Лебедева Ксения' }
  },
  employees(managerOnly = false): EmployeeShort[] {
    return employees.filter((e) => !managerOnly || e.manager?.id === MANAGERS[0].id).map(shortEmployee)
  },
  employee(id: number): Employee {
    const e = employees.find((x) => x.id === id) ?? notFound('Новичок')
    return structuredClone(e)
  },
  createEmployee(input: NewEmployeeInput): NewEmployeeResult {
    const id = Math.max(...employees.map((e) => e.id)) + 1
    const start = new Date(input.start_date)
    const day = Math.round((today.getTime() - firstWorkday(start).getTime()) / 86400_000) + 1
    const manager = MANAGERS.find((m) => m.id === input.manager_id) ?? null
    const recruiter = RECRUITERS.find((r) => r.id === input.recruiter_id) ?? null
    const steps = stepsFor(start, day, input.work_type)
    const meetings = meetingsFor(id, start, day, input.probation_days)
    const code = newCode()
    const e: Employee = {
      id,
      full_name: input.full_name,
      position: input.position,
      site: input.site,
      work_type: input.work_type,
      start_date: input.start_date,
      first_day: iso(firstWorkday(start)),
      day,
      probation_days: input.probation_days,
      current_step: steps.find((s) => s.status === 'current')?.title ?? null,
      pending_surveys: 0,
      open_tickets: 0,
      red_flag: false,
      connected: false,
      next_meeting: meetings.find((m) => !m.done) ?? null,
      synthetic: true,
      manager,
      recruiter,
      invite_link: inviteLink(code),
      intro: null,
      gates: { recruiter_go: false, plan_ready: false },
      plan_step: { at: addDays(firstWorkday(start), 2).toISOString(), sent: false },
      steps,
      surveys: [],
      tickets: [],
      meetings,
    }
    employees.push(e)
    return { employee: shortEmployee(e), code, invite_link: inviteLink(code) }
  },
  openGate(id: number, gate: GateName): Employee {
    const e = employees.find((x) => x.id === id) ?? notFound('Новичок')
    e.gates[gate] = true
    return structuredClone(e)
  },
  planReady(id: number): Employee {
    return mock.openGate(id, 'plan_ready')
  },
  sites(): Site[] {
    return sites.map((s) => ({ ...s }))
  },
  createSite(input: Site): Site {
    const existing = sites.find((s) => s.code === input.code)
    if (existing) Object.assign(existing, input)
    else sites.push({ ...input })
    return { ...input }
  },
  staff(role: StaffRole): StaffMember[] {
    return (role === 'manager' ? MANAGERS : RECRUITERS).map((c) => ({ id: c.id, name: c.name, role }))
  },
  tickets(status?: TicketStatus): TicketShort[] {
    return tickets
      .filter((t) => !status || t.status === status)
      .sort((a, b) => Number(b.red_flag) - Number(a.red_flag) || b.created_at.localeCompare(a.created_at))
      .map(shortTicket)
  },
  ticket(id: number): Ticket {
    const t = tickets.find((x) => x.id === id) ?? notFound('Обращение')
    return structuredClone(t)
  },
  reply(id: number, input: ReplyInput): Ticket {
    const t = tickets.find((x) => x.id === id) ?? notFound('Обращение')
    t.messages.push({ id: messageSeq++, author: 'hr', author_name: 'Лебедева Ксения', text: input.text, created_at: new Date().toISOString(), source: null })
    if (t.status === 'open') {
      t.status = 'in_progress'
      t.assignee = 'Лебедева К.'
    }
    if (input.add_to_kb) {
      documents.push({
        id: docSeq++,
        title: t.preview.slice(0, 80),
        kind: 'answer',
        trade_secret: false,
        status: 'draft',
        version_date: iso(new Date()),
        fragments: 1,
        site: input.kb_site === 'all' ? null : input.kb_site,
        text: input.text,
      })
    }
    recount()
    // все демо-новички синтетические: в «МАКС» ответ не уходит, остаётся в ленте
    return { ...structuredClone(t), synthetic: true, delivered: false, added_to_kb: input.add_to_kb && !t.is_personal }
  },
  setStatus(id: number, status: TicketStatus): Ticket {
    const t = tickets.find((x) => x.id === id) ?? notFound('Обращение')
    t.status = status
    if (status === 'in_progress') t.assignee = 'Лебедева К.'
    recount()
    return structuredClone(t)
  },
  documents(): KbDocument[] {
    return documents.map((d) => ({ ...d }))
  },
  uploadDocument(input: UploadDocumentInput): KbDocument {
    const ext = input.file.name.split('.').pop()?.toLowerCase()
    const chunks = Math.max(6, Math.round(input.file.size / 2500))
    const doc: KbDocument = {
      id: docSeq++,
      title: input.title,
      kind: ext === 'pptx' ? 'pptx' : 'pdf',
      trade_secret: input.is_kt,
      status: 'confirmed',
      version_date: iso(new Date()),
      fragments: chunks,
      chunks,
      site: input.site_codes.length === 1 ? input.site_codes[0] : null,
      sites: input.site_codes,
    }
    documents.unshift(doc)
    return { ...doc }
  },
  confirmAnswer(id: number, answer?: string): KbDocument {
    const d = documents.find((x) => x.id === id) ?? notFound('Ответ')
    d.status = 'confirmed'
    if (answer) d.text = answer
    return { ...d }
  },
  importScenario(): ScenarioReport {
    return {
      steps: 74,
      by_site: { GTP: 31, Luzhniki: 24, GrPP: 27, Kazan: 19 },
      surveys: 6,
      attachments: 11,
      new_sites: sites.some((s) => s.code === 'Kazan') ? [] : ['Kazan'],
      warnings: [
        'Строка 23: нет времени отправки, шаг уйдёт в 10:00',
        'Строка 41: картинка «Изображение 9» не найдена, в сообщении будет пометка «макет»',
        'С 15-го по 52-й день нет ни одного опроса',
      ],
    }
  },
  topics(): Topic[] {
    return topics.map((t) => ({ ...t }))
  },
  shift(to: ShiftTo, employeeId?: number): ShiftResult {
    const e = employees.find((x) => x.id === employeeId) ?? employees[0]
    if (to === 'now') return { employee_id: e.id, day: e.day, message: 'Время вернули к сегодняшнему дню' }
    const target = to === 'end' ? e.probation_days : Number(to)
    e.day = target
    const first = e.full_name.split(' ')[1] ?? e.full_name
    return {
      employee_id: e.id,
      day: e.day,
      message: target < 1 ? `${first}: ${1 - target} дн. до выхода` : `${first}: теперь день ${e.day}`,
    }
  },
  analytics(site?: SiteCode | null): Analytics {
    return mockAnalytics(site)
  },
  risks(site?: SiteCode | null): RiskItem[] {
    return mockRisks(site)
  },
  report(): ReportResult {
    return { ok: true, message: 'Сводка ушла руководителю HR в «МАКС»' }
  },
  meetingDone(employeeId: number, meetingId: number): Employee {
    const e = employees.find((x) => x.id === employeeId) ?? notFound('Новичок')
    const m = e.meetings.find((x) => x.id === meetingId)
    if (m) m.done = true
    e.next_meeting = e.meetings.find((x) => !x.done) ?? null
    return structuredClone(e)
  },
}
