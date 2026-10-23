// Контракт данных Mini App ↔ бэкенд. Описание эндпоинтов — miniapp/API.md

export type Role = 'hr' | 'manager' | 'head'

export interface Me {
  role: Role
  name: string
  demo?: boolean // демо-кнопки на сервере (DEMO_ENABLED); нет поля — узнаём по ответу демо-эндпоинта
}

/** Код площадки: GTP, Luzhniki, GrPP и новые из xlsx сценария */
export type SiteCode = string

export interface Site {
  code: SiteCode
  name: string
  tz: string // Europe/Moscow
}

export type StaffRole = 'manager' | 'recruiter'

/** Справочник для формы «Завести новичка» */
export interface StaffMember {
  id: number
  name: string
  role: string
}

export type WorkType = 'office' | 'production'

export interface Contact {
  id: number
  name: string
  role: string
  phone?: string | null
}

/** Ячейка списка «Новички» */
export interface EmployeeShort {
  id: number
  full_name: string
  position: string
  site: SiteCode
  work_type: WorkType
  start_date: string // YYYY-MM-DD, как завёл HR
  /** первый рабочий день: выход в выходной переносится на понедельник, от него считается «день N» */
  first_day: string // YYYY-MM-DD
  day: number // день адаптации, с 1
  probation_days: number
  current_step: string | null
  pending_surveys: number
  open_tickets: number
  red_flag: boolean
  connected: boolean // подключился ли к боту в «МАКС»
  next_meeting: Meeting | null
  synthetic: boolean
  test?: boolean // тестовый новичок команды: только его можно перематывать машиной времени
}

export interface Step {
  day?: number | null
  title: string
  date?: string | null // YYYY-MM-DD или ISO
  status?: 'done' | 'current' | 'upcoming'
}

export interface SurveyItem {
  id: number
  title: string
  day: number
  status: 'done' | 'pending' | 'missed'
  score: number | null // средняя оценка, 1–10
}

export interface Meeting {
  id: number
  day: number
  title: string
  date: string
  done: boolean
}

/** «Представление коллегам»: текст и фото от новичка */
export interface Intro {
  text: string
  photo_url: string | null
  at: string | null // ISO
}

/** Отмашки: шаги сценария, которые ждут HR или руководителя */
export interface Gates {
  recruiter_go: boolean // «представлю тебя коллегам»
  plan_ready: boolean // адаптационный план отправлен
}

export type GateName = keyof Gates

/** Шаг сценария «Адаптационный план»: файл уходит вместе с ним, а не в момент «План готов» */
export interface PlanStep {
  at: string | null // ISO: когда шаг уходит по сценарию; null — шага плана для новичка нет
  sent: boolean // шаг уже ушёл новичку
}

/** Карточка новичка */
export interface Employee extends EmployeeShort {
  manager: Contact | null
  recruiter: Contact | null
  invite_link: string | null
  intro: Intro | null
  gates: Gates
  plan_step: PlanStep
  steps: Step[]
  surveys: SurveyItem[]
  tickets: TicketShort[]
  meetings: Meeting[]
}

export interface NewEmployeeInput {
  full_name: string
  position: string
  site: SiteCode
  work_type: WorkType
  start_date: string
  probation_days: number
  manager_id: number | null
  recruiter_id: number | null
}

export interface NewEmployeeResult {
  employee: EmployeeShort
  invite_link: string // https://max.ru/<bot>?start=<code>
  code: string
}

export type TicketStatus = 'open' | 'in_progress' | 'closed'

export type TicketCategory =
  | 'safety' // охрана труда, красный флаг
  | 'harassment' // травля, красный флаг
  | 'intent_to_leave' // намерение уйти, красный флаг
  | 'health' // здоровье, красный флаг
  | 'unknown' // бот не нашёл ответ → HR
  | 'contact_request' // просьба связаться
  | 'question' // вопрос HR (отпуск, зарплата, документы)

export interface TicketShort {
  id: number
  employee_id: number
  employee_name: string
  site: SiteCode
  day: number
  category: TicketCategory
  category_label: string
  red_flag: boolean
  status: TicketStatus
  preview: string // первая строка цитаты
  created_at: string // ISO
  due_at: string | null // срок ответа, ISO
}

export interface Message {
  id: number
  author: 'employee' | 'bot' | 'hr'
  author_name: string
  text: string
  created_at: string
  source: string | null // «ПВТР, п. 6.5.2, стр. 15» у ответов бота
}

export interface Ticket extends TicketShort {
  assignee: string | null
  messages: Message[]
  is_personal?: boolean // личный вопрос: ответ в базу не добавляется
  synthetic?: boolean // демо-новичок: в «МАКС» ему ничего не уходит
  delivered?: boolean // ответ на reply: ушло ли сообщение новичку в «МАКС»
  added_to_kb?: boolean // ответ на reply: создан черновик в базе
}

export interface ReplyInput {
  text: string
  add_to_kb: boolean
  kb_site: SiteCode | 'all' | null
}

export type KbKind = 'pdf' | 'pptx' | 'xlsx' | 'md' | 'answer'

export interface KbDocument {
  id: number
  title: string
  kind: KbKind
  trade_secret: boolean // метка «КТ»: фрагменты не уходят в LLM
  status: 'draft' | 'confirmed' // черновик HR / подтверждён
  version_date: string
  fragments: number
  chunks?: number // ответ на загрузку: сколько фрагментов получилось
  site: SiteCode | null // null — для всех площадок
  sites?: SiteCode[] // площадки документа; пусто — все
  text?: string | null // текст ответа HR (для вкладки «Ответы HR»)
}

export interface UploadDocumentInput {
  file: File
  title: string
  is_kt: boolean
  site_codes: SiteCode[] // пусто — все площадки
}

/** Отчёт об импорте xlsx сценария */
export interface ScenarioReport {
  steps: number
  by_site: Record<SiteCode, number>
  surveys: number
  attachments: number
  new_sites: SiteCode[]
  warnings: string[]
}

export interface Topic {
  topic: string
  count: number
  peak_days: string // «дни 1–3»
}

export type ShiftTo = '-2' | '1' | '3' | '14' | '60' | 'end' | 'now'

export interface ShiftResult {
  employee_id: number
  day: number
  message: string
}

export interface ReportResult {
  ok: boolean
  message: string
}
