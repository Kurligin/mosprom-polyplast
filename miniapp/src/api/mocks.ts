// Демо-данные. Все люди и обращения — синтетические.
import type {
  Contact, Employee, EmployeeShort, KbDocument, Me, Meeting, Message, NewEmployeeInput, NewEmployeeResult,
  ReplyInput, ReportResult, Role, ShiftResult, ShiftTo, Site, SiteCode, Step, SurveyItem, Ticket, TicketShort,
  TicketStatus, Topic, WorkType,
} from './types'

export const SITES: Site[] = [
  { code: 'GTP', name: 'GTP' },
  { code: 'Luzhniki', name: 'Luzhniki' },
  { code: 'GrPP', name: 'GrPP' },
]

const today = new Date()
today.setHours(9, 0, 0, 0)
const iso = (d: Date) => d.toISOString().slice(0, 10)
const addDays = (d: Date, n: number) => {
  const r = new Date(d)
  r.setDate(r.getDate() + n)
  return r
}
const hoursAgo = (h: number) => new Date(Date.now() - h * 3600_000).toISOString()
const hoursAhead = (h: number) => new Date(Date.now() + h * 3600_000).toISOString()

export const MANAGERS: Contact[] = [
  { id: 101, name: 'Соколов Андрей', role: 'Руководитель, производство GTP', phone: '+7 900 000-01-01' },
  { id: 102, name: 'Белова Ирина', role: 'Руководитель, офис Luzhniki', phone: '+7 900 000-01-02' },
  { id: 103, name: 'Гусев Павел', role: 'Руководитель, производство GrPP', phone: '+7 900 000-01-03' },
]
export const RECRUITERS: Contact[] = [
  { id: 201, name: 'Миронова Ольга', role: 'Рекрутер', phone: '+7 900 000-02-01' },
  { id: 202, name: 'Ковалёв Денис', role: 'Рекрутер', phone: '+7 900 000-02-02' },
]

type Seed = [name: string, position: string, site: SiteCode, work: WorkType, day: number, probation: number, flags: Partial<EmployeeShort>]

const SEED: Seed[] = [
  ['Петров Илья', 'Оператор экструдера', 'GTP', 'production', 3, 90, { pending_surveys: 1 }],
  ['Захарова Мария', 'Инженер-технолог', 'GTP', 'production', 12, 90, { open_tickets: 1, red_flag: true }],
  ['Орлов Никита', 'Наладчик оборудования', 'GTP', 'production', 34, 90, {}],
  ['Кузнецова Анна', 'Лаборант ОТК', 'GTP', 'production', 61, 90, { pending_surveys: 1 }],
  ['Фёдоров Артём', 'Кладовщик', 'GTP', 'production', 1, 90, { connected: false }],
  ['Смирнова Елена', 'Бухгалтер', 'Luzhniki', 'office', 5, 90, { open_tickets: 1 }],
  ['Волков Дмитрий', 'Менеджер по закупкам', 'Luzhniki', 'office', 18, 90, {}],
  ['Лебедева Ксения', 'HR-специалист', 'Luzhniki', 'office', 2, 90, { pending_surveys: 1 }],
  ['Никитин Роман', 'Аналитик продаж', 'Luzhniki', 'office', 47, 180, { open_tickets: 1 }],
  ['Морозова Дарья', 'Юрист', 'Luzhniki', 'office', 85, 90, {}],
  ['Алексеев Сергей', 'Оператор линии', 'GrPP', 'production', 7, 90, { open_tickets: 1 }],
  ['Тихонова Вера', 'Мастер смены', 'GrPP', 'production', 26, 90, { pending_surveys: 2 }],
  ['Егоров Максим', 'Слесарь-ремонтник', 'GrPP', 'production', 4, 90, {}],
  ['Павлова Светлана', 'Специалист по ОТ', 'GrPP', 'production', 58, 90, {}],
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

const employees: Employee[] = SEED.map(([full_name, position, site, work_type, day, probation_days, flags], i) => {
  const id = i + 1
  const start = addDays(today, -(day - 1))
  const meetings = meetingsFor(id, start, day, probation_days)
  const steps = stepsFor(start, day, work_type)
  const pending = flags.pending_surveys ?? 0
  return {
    id,
    full_name,
    position,
    site,
    work_type,
    start_date: iso(start),
    day,
    probation_days,
    current_step: steps.find((s) => s.status === 'current')?.title ?? null,
    pending_surveys: pending,
    open_tickets: 0,
    red_flag: false,
    connected: flags.connected ?? true,
    next_meeting: meetings.find((m) => !m.done) ?? null,
    synthetic: true,
    manager: managerFor(site),
    recruiter: RECRUITERS[i % 2],
    intro_text:
      day >= 2
        ? `Всем привет! Я ${full_name.split(' ')[1]}, ${position.toLowerCase()}. Рад${full_name.endsWith('а') ? 'а' : ''} присоединиться к команде ${site}. Люблю разбираться, как всё устроено.`
        : null,
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
    msg('bot', 'Корсик', 'Спасибо, что написали. Это важно — передал HR, сообщение увидит только HR. Если опасность прямо сейчас — остановите работу и сообщите мастеру смены или по телефону охраны труда.', 1),
  ]),
  makeTicket(6, 'question', 'Отпуск', false, 'open', 5, 19, [
    msg('employee', 'Смирнова Елена', 'Можно ли взять отпуск за свой счёт на 3 дня в ноябре, если я работаю меньше месяца?', 5),
    msg('bot', 'Корсик', 'В правилах есть пункт об отпуске без сохранения зарплаты:\n\n«По семейным обстоятельствам и другим уважительным причинам работнику по его письменному заявлению может быть предоставлен отпуск без сохранения заработной платы…»', 5, 'ПВТР, п. 6.9, стр. 18'),
    msg('employee', 'Смирнова Елена', 'А кому писать заявление и за сколько дней?', 4),
    msg('bot', 'Корсик', 'Точного ответа в документах не нашёл. Передал вопрос HR — ответ придёт сюда.', 4),
  ]),
  makeTicket(11, 'unknown', 'Не знаю → HR', false, 'open', 8, -2, [
    msg('employee', 'Алексеев Сергей', 'Где получить вторую пару спецобуви? Первая промокла, а смена завтра в 7.', 8),
    msg('bot', 'Корсик', 'Не нашёл ответа в документах базы. Передал вопрос HR — ответ придёт сюда. Можете задать другой вопрос.', 8),
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
    msg('hr', 'Лебедева Ксения', 'Аванс — 25-го, зарплата — 10-го числа. Если выпадает на выходной — накануне.', 70),
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
  const { manager: _1, recruiter: _2, intro_text: _3, steps: _4, surveys: _5, tickets: _6, meetings: _7, ...rest } = e
  void _1; void _2; void _3; void _4; void _5; void _6; void _7
  return rest
}

recount()

const documents: KbDocument[] = [
  { id: 1, title: 'Правила внутреннего трудового распорядка П 3.11-07', kind: 'pdf', trade_secret: false, status: 'confirmed', version_date: '2025-03-12', fragments: 142, site: null },
  { id: 2, title: 'Положение о коммерческой тайне', kind: 'pptx', trade_secret: true, status: 'confirmed', version_date: '2025-01-20', fragments: 38, site: null },
  { id: 3, title: 'Антикоррупционная политика', kind: 'pptx', trade_secret: false, status: 'confirmed', version_date: '2024-11-05', fragments: 27, site: null },
  { id: 4, title: 'Политика в области качества и экологии', kind: 'pptx', trade_secret: false, status: 'confirmed', version_date: '2024-09-18', fragments: 19, site: null },
  { id: 5, title: 'Сценарий адаптации «Корсик», 68 шагов', kind: 'xlsx', trade_secret: false, status: 'confirmed', version_date: '2025-09-15', fragments: 68, site: null },
  { id: 6, title: 'Аванс и зарплата: числа выплат', kind: 'answer', trade_secret: false, status: 'confirmed', version_date: iso(addDays(today, -3)), fragments: 1, site: 'GTP' },
  { id: 7, title: 'Где получить вторую пару спецобуви', kind: 'answer', trade_secret: false, status: 'draft', version_date: iso(today), fragments: 1, site: 'GrPP' },
]

const topics: Topic[] = [
  { topic: 'Пропуск и проход', count: 34, peak_days: 'дни 1–3' },
  { topic: 'Зарплата и аванс', count: 27, peak_days: 'дни 10–20' },
  { topic: 'Спецодежда и СИЗ', count: 21, peak_days: 'дни 1–7' },
  { topic: 'Отпуск', count: 15, peak_days: 'дни 30–60' },
  { topic: 'Обучение и инструктажи', count: 12, peak_days: 'дни 1–14' },
  { topic: 'Питание и столовая', count: 9, peak_days: 'дни 1–5' },
]

export const mock = {
  me(roleParam: string | null): Me {
    const role: Role = roleParam === 'manager' ? 'manager' : roleParam === 'head' ? 'head' : 'hr'
    return role === 'manager' ? { role, name: 'Соколов Андрей' } : { role, name: 'Лебедева Ксения' }
  },
  employees(managerOnly = false): EmployeeShort[] {
    return employees.filter((e) => !managerOnly || e.manager.id === MANAGERS[0].id).map(shortEmployee)
  },
  employee(id: number): Employee {
    const e = employees.find((x) => x.id === id)
    if (!e) throw new Error('not found')
    return structuredClone(e)
  },
  createEmployee(input: NewEmployeeInput): NewEmployeeResult {
    const id = Math.max(...employees.map((e) => e.id)) + 1
    const start = new Date(input.start_date)
    const day = Math.max(0, Math.round((today.getTime() - start.getTime()) / 86400_000) + 1)
    const manager = MANAGERS.find((m) => m.id === input.manager_id) ?? MANAGERS[0]
    const recruiter = RECRUITERS.find((r) => r.id === input.recruiter_id) ?? RECRUITERS[0]
    const steps = stepsFor(start, day, input.work_type)
    const meetings = meetingsFor(id, start, day, input.probation_days)
    const e: Employee = {
      id,
      full_name: input.full_name,
      position: input.position,
      site: input.site,
      work_type: input.work_type,
      start_date: input.start_date,
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
      intro_text: null,
      steps,
      surveys: [],
      tickets: [],
      meetings,
    }
    employees.push(e)
    const code = Math.random().toString(36).slice(2, 10).toUpperCase()
    return { employee: shortEmployee(e), code, invite_link: `https://max.ru/korsik_bot?start=${code}` }
  },
  tickets(status?: TicketStatus): TicketShort[] {
    return tickets
      .filter((t) => !status || t.status === status)
      .sort((a, b) => Number(b.red_flag) - Number(a.red_flag) || b.created_at.localeCompare(a.created_at))
      .map(shortTicket)
  },
  ticket(id: number): Ticket {
    const t = tickets.find((x) => x.id === id)
    if (!t) throw new Error('not found')
    return structuredClone(t)
  },
  reply(id: number, input: ReplyInput): Ticket {
    const t = tickets.find((x) => x.id === id)!
    t.messages.push({ id: messageSeq++, author: 'hr', author_name: 'Лебедева Ксения', text: input.text, created_at: new Date().toISOString(), source: null })
    if (t.status === 'open') {
      t.status = 'in_progress'
      t.assignee = 'Лебедева К.'
    }
    if (input.add_to_kb) {
      documents.push({
        id: documents.length + 1,
        title: t.preview.slice(0, 60),
        kind: 'answer',
        trade_secret: false,
        status: 'draft',
        version_date: iso(new Date()),
        fragments: 1,
        site: input.kb_site === 'all' ? null : input.kb_site,
      })
    }
    recount()
    return structuredClone(t)
  },
  setStatus(id: number, status: TicketStatus): Ticket {
    const t = tickets.find((x) => x.id === id)!
    t.status = status
    if (status === 'in_progress') t.assignee = 'Лебедева К.'
    recount()
    return structuredClone(t)
  },
  documents(): KbDocument[] {
    return [...documents]
  },
  topics(): Topic[] {
    return topics
  },
  shift(to: ShiftTo): ShiftResult {
    const e = employees[0]
    const target = to === 'end' ? e.probation_days : to === '-2' ? -2 : Number(to)
    e.day = Math.max(target, -2)
    return {
      employee_id: e.id,
      day: e.day,
      message: target < 1 ? `${e.full_name}: за ${-target + 1} дн. до выхода` : `${e.full_name}: день ${e.day}`,
    }
  },
  report(): ReportResult {
    return { ok: true, message: 'Сводка отправлена главе подбора в «МАКС»' }
  },
  meetingDone(employeeId: number, meetingId: number): Employee {
    const e = employees.find((x) => x.id === employeeId)!
    const m = e.meetings.find((x) => x.id === meetingId)
    if (m) m.done = true
    e.next_meeting = e.meetings.find((x) => !x.done) ?? null
    return structuredClone(e)
  },
}
