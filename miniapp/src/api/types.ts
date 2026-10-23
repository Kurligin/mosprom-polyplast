// Контракт данных Mini App ↔ бэкенд. Описание эндпоинтов — miniapp/API.md

export type Role = 'hr' | 'manager' | 'head'

export interface Me {
  role: Role
  name: string
}

export type SiteCode = 'GTP' | 'Luzhniki' | 'GrPP'

export interface Site {
  code: SiteCode
  name: string
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
  start_date: string // YYYY-MM-DD
  day: number // день адаптации, с 1
  probation_days: number
  current_step: string | null
  pending_surveys: number
  open_tickets: number
  red_flag: boolean
  connected: boolean // подключился ли к боту в «МАКС»
  next_meeting: Meeting | null
  synthetic: boolean
}

export interface Step {
  day: number
  title: string
  date: string
  status: 'done' | 'current' | 'upcoming'
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

/** Карточка новичка */
export interface Employee extends EmployeeShort {
  manager: Contact
  recruiter: Contact
  intro_text: string | null // «представление коллегам»
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
  manager_id: number
  recruiter_id: number
}

export interface NewEmployeeResult {
  employee: EmployeeShort
  invite_link: string // https://max.ru/<bot>?start=<code>
  code: string
}

export type TicketStatus = 'open' | 'in_progress' | 'closed'

export type TicketCategory =
  | 'safety' // охрана труда — красный флаг
  | 'harassment' // травля — красный флаг
  | 'intent_to_leave' // намерение уйти — красный флаг
  | 'health' // здоровье — красный флаг
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
  source: string | null // «ПВТР, п. 6.5.2, стр. 15» — у ответов бота
}

export interface Ticket extends TicketShort {
  assignee: string | null
  messages: Message[]
}

export interface ReplyInput {
  text: string
  add_to_kb: boolean
  kb_site: SiteCode | 'all' | null
}

export interface KbDocument {
  id: number
  title: string
  kind: 'pdf' | 'pptx' | 'xlsx' | 'answer'
  trade_secret: boolean // метка «КТ»: фрагменты не уходят в LLM
  status: 'draft' | 'confirmed' // черновик HR / подтверждён
  version_date: string
  fragments: number
  site: SiteCode | null // null — для всех площадок
}

export interface Topic {
  topic: string
  count: number
  peak_days: string // «дни 1–3»
}

export type ShiftTo = '-2' | '1' | '3' | '14' | '60' | 'end'

export interface ShiftResult {
  employee_id: number
  day: number
  message: string
}

export interface ReportResult {
  ok: boolean
  message: string
}
