import { initDataHeader, isInMax } from '../lib/bridge'
import { mock } from './mocks'
import type {
  Analytics, Employee, EmployeeShort, GateName, KbDocument, Me, NewEmployeeInput, NewEmployeeResult, ReplyInput,
  ReportResult, RiskItem, ScenarioReport, ShiftResult, ShiftTo, Site, SiteCode, StaffMember, StaffRole, Ticket,
  TicketShort, TicketStatus, Topic, UploadDocumentInput,
} from './types'

/*
 * Клиент ходит в настоящий API. Демо-данные берутся, только если:
 *  - VITE_USE_MOCKS=1 или ?mocks=1 в адресе;
 *  - сервер недоступен (сеть, 5xx, вместо JSON пришло что-то другое);
 *  - эндпоинт ещё не готов (501 или маршрута нет).
 * Демо-ответы помечаются, экран показывает плашку «демо-данные».
 * Действия (POST) уходят в демо, только если экран сам показывает демо-данные.
 */

const params = new URLSearchParams(location.search)
export const roleParam = params.get('role')

/**
 * «Посмотреть глазами руководителя» (Ещё → Режим, ?role=manager). Внутри «МАКС» initData — от HR,
 * поэтому просим бэкенд ответить как руководитель: view=manager. Бэкенд отдаёт список и карточки
 * руководителя без опросов и обращений; настоящему руководителю параметр ничего не меняет.
 */
export const viewAsManager = roleParam === 'manager'

/** Демо-кнопки на сервере: true / false из /api/me, null — сервер не сказал (узнаем по 404 демо-эндпоинта) */
export let demoEnabled: boolean | null = null
const withView = (path: string) => (viewAsManager ? `${path}${path.includes('?') ? '&' : '?'}view=manager` : path)

/** Демо принудительно: VITE_USE_MOCKS=1 при сборке или ?mocks=1 в адресе */
export const FORCE_MOCKS =
  import.meta.env.VITE_USE_MOCKS === '1' || params.get('mocks') === '1' || (import.meta.env.DEV && params.get('mocks') === 'worst')

/** Ошибка для человека: текст из `detail` или понятная замена */
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}

export function toApiError(e: unknown): ApiError {
  if (e instanceof ApiError) return e
  if (e instanceof Error && e.message) return new ApiError(0, e.message)
  return new ApiError(0, 'Что-то пошло не так')
}

const demoData = new WeakSet<object>()
/** Данные пришли из демо, а не с сервера */
export const isDemoData = (d: unknown) => typeof d === 'object' && d !== null && demoData.has(d)
function markDemo<T>(d: T): T {
  if (typeof d === 'object' && d !== null) demoData.add(d)
  return d
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

type Outcome =
  | { kind: 'ok'; data: unknown }
  | { kind: 'unavailable' } // сеть, 5xx, не JSON
  | { kind: 'not_ready'; detail: string | null } // 501 или маршрута нет
  | { kind: 'error'; status: number; detail: string | null }

const STATUS_TEXT: Record<number, string> = {
  400: 'Сервер не принял данные. Проверьте поля',
  401: 'Откройте панель из чата с Корсиком в «МАКС»',
  403: 'Нет доступа к этому разделу',
  404: 'Не нашли. Возможно, запись уже удалили',
  409: 'Такая запись уже есть',
  413: 'Файл слишком большой',
  415: 'Этот формат файла не подходит',
  422: 'Сервер не принял данные. Проверьте поля',
}

function detailText(body: unknown): string | null {
  if (!body || typeof body !== 'object') return null
  const d = (body as { detail?: unknown }).detail
  if (typeof d === 'string') return d
  if (Array.isArray(d) && d.length && typeof d[0]?.msg === 'string') return d[0].msg // ошибки валидации FastAPI
  return null
}

function classify(status: number, isJson: boolean, body: unknown): Outcome {
  const detail = detailText(body)
  if (status >= 200 && status < 300) return isJson ? { kind: 'ok', data: body } : { kind: 'unavailable' }
  if (status === 501) return { kind: 'not_ready', detail }
  // маршрута ещё нет: FastAPI отвечает 404 / 405 со стандартным текстом
  if ((status === 404 && detail === 'Not Found') || status === 405) return { kind: 'not_ready', detail: null }
  if (status >= 500 || status === 0) return { kind: 'unavailable' }
  return { kind: 'error', status, detail }
}

/**
 * Заголовки каждого запроса. X-Max-Init-Data — подпись «МАКС», по ней бэкенд узнаёт человека и роль.
 * ?role=manager вне «МАКС» (в разработке) просит бэкенд ответить как первый руководитель: X-Dev-Role.
 */
function baseHeaders(): Record<string, string> {
  const h: Record<string, string> = { 'X-Max-Init-Data': initDataHeader(), Accept: 'application/json' }
  if (!isInMax() && (roleParam === 'manager' || roleParam === 'head' || roleParam === 'hr')) h['X-Dev-Role'] = roleParam
  return h
}

async function send(method: 'GET' | 'POST', path: string, body?: unknown): Promise<Outcome> {
  const headers: Record<string, string> = { ...baseHeaders() }
  if (body !== undefined) headers['Content-Type'] = 'application/json'
  let res: Response
  try {
    res = await fetch(`/api${path}`, { method, headers, body: body !== undefined ? JSON.stringify(body) : undefined })
  } catch {
    return { kind: 'unavailable' }
  }
  const isJson = (res.headers.get('content-type') ?? '').includes('application/json')
  let parsed: unknown = null
  if (isJson) {
    try {
      parsed = await res.json()
    } catch {
      return { kind: 'unavailable' }
    }
  }
  return classify(res.status, isJson, parsed)
}

/** multipart с прогрессом отправки (fetch прогресс не умеет) */
function sendForm(path: string, form: FormData, onProgress?: (p: number) => void): Promise<Outcome> {
  return new Promise((resolve) => {
    const xhr = new XMLHttpRequest()
    xhr.open('POST', `/api${path}`)
    Object.entries(baseHeaders()).forEach(([k, v]) => xhr.setRequestHeader(k, v))
    xhr.upload.onprogress = (e) => {
      if (e.lengthComputable && onProgress) onProgress(e.loaded / e.total)
    }
    xhr.onload = () => {
      const isJson = (xhr.getResponseHeader('content-type') ?? '').includes('application/json')
      let parsed: unknown = null
      if (isJson) {
        try {
          parsed = JSON.parse(xhr.responseText)
        } catch {
          return resolve({ kind: 'unavailable' })
        }
      }
      resolve(classify(xhr.status, isJson, parsed))
    }
    xhr.onerror = () => resolve({ kind: 'unavailable' })
    xhr.ontimeout = () => resolve({ kind: 'unavailable' })
    xhr.send(form)
  })
}

async function fromMock<T>(fallback: () => T): Promise<T> {
  await wait(160)
  return markDemo(fallback())
}

/** Чтение: при недоступном сервере или неготовом эндпоинте берём демо */
async function get<T>(path: string, fallback: () => T): Promise<T> {
  if (FORCE_MOCKS) return fromMock(fallback)
  const r = await send('GET', path)
  if (r.kind === 'ok') return r.data as T
  if (r.kind === 'error') throw new ApiError(r.status, r.detail ?? STATUS_TEXT[r.status] ?? 'Не получилось загрузить')
  return fromMock(fallback)
}

export type Opts = { demo?: boolean }

function failWrite(r: Exclude<Outcome, { kind: 'ok' }>): never {
  if (r.kind === 'unavailable') throw new ApiError(0, 'Сервер не отвечает. Проверьте связь и попробуйте ещё раз')
  if (r.kind === 'not_ready') throw new ApiError(501, r.detail ?? 'Эта функция на сервере пока не готова')
  throw new ApiError(r.status, r.detail ?? STATUS_TEXT[r.status] ?? 'Не получилось. Попробуйте ещё раз')
}

/** Действие: настоящий запрос; демо — только если экран на демо-данных */
async function post<T>(path: string, body: unknown, fallback: () => T, o?: Opts): Promise<T> {
  if (FORCE_MOCKS || o?.demo) return fromMock(fallback)
  const r = await send('POST', path, body)
  if (r.kind === 'ok') return r.data as T
  return failWrite(r)
}

async function upload<T>(path: string, form: FormData, fallback: () => T, o?: Opts & { onProgress?: (p: number) => void }): Promise<T> {
  if (FORCE_MOCKS || o?.demo) {
    // в демо показываем такой же прогресс, как у настоящей загрузки
    for (let i = 1; i <= 10; i++) {
      await wait(90)
      o?.onProgress?.(i / 10)
    }
    await wait(700)
    return markDemo(fallback())
  }
  const r = await sendForm(path, form, o?.onProgress)
  if (r.kind === 'ok') return r.data as T
  return failWrite(r)
}

/* ---------- Нормализация: бэкенд пишется параллельно, страхуемся от пропусков ---------- */

type Raw = Record<string, unknown>

function normEmployee<T extends EmployeeShort>(raw: T): T {
  const e = raw as T & Partial<Employee> & Raw
  const out = {
    ...e,
    position: e.position ?? '',
    first_day: e.first_day ?? e.start_date,
    current_step: e.current_step ?? null,
    pending_surveys: e.pending_surveys ?? 0,
    open_tickets: e.open_tickets ?? 0,
    red_flag: !!e.red_flag,
    connected: !!e.connected,
    next_meeting: e.next_meeting ?? null,
    probation_days: e.probation_days || 90,
    day: typeof e.day === 'number' ? e.day : 0,
  } as T
  if ('steps' in e || 'meetings' in e || 'manager' in e) {
    const full = out as unknown as Employee
    const introText = typeof e.intro_text === 'string' ? e.intro_text : null
    full.manager = e.manager ?? null
    full.recruiter = e.recruiter ?? null
    full.invite_link = e.invite_link ?? null
    full.intro = e.intro ?? (introText ? { text: introText, photo_url: null, at: null } : null)
    full.gates = { recruiter_go: !!e.gates?.recruiter_go, plan_ready: !!e.gates?.plan_ready }
    full.plan_step = { at: e.plan_step?.at ?? null, sent: !!e.plan_step?.sent }
    full.steps = e.steps ?? []
    full.surveys = e.surveys ?? []
    full.tickets = e.tickets ?? []
    full.chats = e.chats ?? []
    full.meetings = e.meetings ?? []
  }
  if (isDemoData(raw)) markDemo(out)
  return out
}

function normEmployees(list: EmployeeShort[]): EmployeeShort[] {
  const out = (Array.isArray(list) ? list : []).map(normEmployee)
  if (isDemoData(list)) markDemo(out)
  return out
}

const KIND_MAP: Record<string, KbDocument['kind']> = { hr_answer: 'answer', answer: 'answer', pdf: 'pdf', pptx: 'pptx', xlsx: 'xlsx', md: 'md' }

function normDoc(raw: KbDocument): KbDocument {
  const d = raw as KbDocument & Raw
  const kind = KIND_MAP[String(d.kind ?? d.doc_type ?? 'pdf')] ?? 'pdf'
  const siteCodes = Array.isArray(d.sites) ? (d.sites as string[]) : Array.isArray(d.site_codes) ? (d.site_codes as string[]) : d.site ? [d.site] : []
  const out: KbDocument = {
    ...d,
    kind,
    trade_secret: !!(d.trade_secret ?? d.is_kt),
    status: d.status === 'draft' ? 'draft' : 'confirmed',
    version_date: d.version_date ?? (d.created_at as string) ?? '',
    fragments: d.fragments ?? d.chunks ?? 0,
    site: d.site ?? (siteCodes.length === 1 ? siteCodes[0] : null),
    sites: siteCodes,
    text: d.text ?? (typeof d.answer === 'string' ? d.answer : null),
  }
  if (isDemoData(raw)) markDemo(out)
  return out
}

function normDocs(list: KbDocument[]): KbDocument[] {
  const out = (Array.isArray(list) ? list : []).map(normDoc)
  if (isDemoData(list)) markDemo(out)
  return out
}

function normReport(raw: ScenarioReport): ScenarioReport {
  const r = raw as Partial<ScenarioReport>
  const out: ScenarioReport = {
    steps: r.steps ?? 0,
    by_site: r.by_site ?? {},
    surveys: r.surveys ?? 0,
    attachments: r.attachments ?? 0,
    new_sites: r.new_sites ?? [],
    warnings: r.warnings ?? [],
  }
  if (isDemoData(raw)) markDemo(out)
  return out
}

const arr = <T,>(v: T[] | null | undefined): T[] => (Array.isArray(v) ? v : [])

function normAnalytics(raw: Analytics): Analytics {
  const a = raw as Partial<Analytics>
  const out: Analytics = {
    ...(raw as Analytics),
    funnel: arr(a.funnel),
    clusters: arr(a.clusters).map((c) => ({ ...c, sites: arr(c.sites), quotes: arr(c.quotes), quotes_reason: c.quotes_reason ?? null })),
    clusters_low_data: a.clusters_low_data ?? false,
    mood: { sites: arr(a.mood?.sites), low_data: arr(a.mood?.low_data), weeks: arr(a.mood?.weeks) },
    risks: { total: a.risks?.total ?? 0, high: a.risks?.high ?? 0, medium: a.risks?.medium ?? 0, reasons: arr(a.risks?.reasons) },
    sites: arr(a.sites),
    threshold: a.threshold ?? 5,
  }
  if (isDemoData(raw)) markDemo(out)
  return out
}

function normRisks(list: RiskItem[]): RiskItem[] {
  const out = arr(list).map((r) => ({ ...r, reasons: arr(r.reasons) }))
  if (isDemoData(list)) markDemo(out)
  return out
}

const siteQuery = (site?: SiteCode | null) => (site ? `?site=${encodeURIComponent(site)}` : '')

/* ---------- Справочник площадок: один на всё приложение ---------- */

let sitesCache: Promise<Site[]> | null = null
export function loadSites(force = false): Promise<Site[]> {
  if (!sitesCache || force) {
    sitesCache = get<Site[]>('/sites', () => mock.sites()).catch((e) => {
      sitesCache = null
      throw e
    })
  }
  return sitesCache
}

/* ---------- API ---------- */

export const api = {
  me: () =>
    get<Me>('/me', () => mock.me(roleParam)).then((me): Me => {
      if (typeof me.demo === 'boolean') demoEnabled = me.demo
      if (viewAsManager) return { ...me, role: 'manager' }
      // режим HR вручную — только в разработке: руководителю в «МАКС» бэкенд всё равно ответит 403
      if (roleParam === 'hr' && !isInMax()) return { ...me, role: 'hr' }
      return me
    }),

  employees: (opts: { mine?: boolean } = {}) =>
    get<EmployeeShort[]>(withView(`/employees${opts.mine ? '?mine=1' : ''}`), () => mock.employees(opts.mine)).then(normEmployees),
  employee: (id: number) => get<Employee>(withView(`/employees/${id}`), () => mock.employee(id)).then(normEmployee),
  createEmployee: (input: NewEmployeeInput, o?: Opts) =>
    post<NewEmployeeResult>('/employees', input, () => mock.createEmployee(input), o),
  openGate: (id: number, gate: GateName, o?: Opts) =>
    post<Employee>(`/employees/${id}/gate`, { gate }, () => mock.openGate(id, gate), o).then(normEmployee),
  uploadPlan: (id: number, file: File, o?: Opts & { onProgress?: (p: number) => void }) => {
    const form = new FormData()
    form.append('file', file)
    return upload<Employee>(withView(`/employees/${id}/plan`), form, () => mock.planReady(id), o).then(normEmployee)
  },
  meetingDone: (employeeId: number, meetingId: number, o?: Opts) =>
    post<Employee>(withView(`/meetings/${meetingId}/done`), {}, () => mock.meetingDone(employeeId, meetingId), o).then(normEmployee),

  sites: () => loadSites(),
  createSite: (input: Site, o?: Opts) => post<Site>('/sites', input, () => mock.createSite(input), o),
  staff: (role: StaffRole) => get<StaffMember[]>(`/staff?role=${role}`, () => mock.staff(role)),

  tickets: (status?: TicketStatus) =>
    get<TicketShort[]>(`/tickets${status ? `?status=${status}` : ''}`, () => mock.tickets(status)),
  ticket: (id: number) => get<Ticket>(`/tickets/${id}`, () => mock.ticket(id)),
  reply: (id: number, input: ReplyInput, o?: Opts) =>
    post<Ticket>(`/tickets/${id}/reply`, input, () => mock.reply(id, input), o),
  takeTicket: (id: number, o?: Opts) => post<Ticket>(`/tickets/${id}/take`, {}, () => mock.setStatus(id, 'in_progress'), o),
  closeTicket: (id: number, o?: Opts) => post<Ticket>(`/tickets/${id}/close`, {}, () => mock.setStatus(id, 'closed'), o),

  documents: () => get<KbDocument[]>('/kb/documents', () => mock.documents()).then(normDocs),
  uploadDocument: (input: UploadDocumentInput, o?: Opts & { onProgress?: (p: number) => void }) => {
    const form = new FormData()
    form.append('file', input.file)
    form.append('title', input.title)
    form.append('is_kt', input.is_kt ? 'true' : 'false')
    form.append('site_codes', input.site_codes.join(','))
    return upload<KbDocument>('/kb/documents', form, () => mock.uploadDocument(input), o).then(normDoc)
  },
  /** answer — исправленный текст ответа (необязательно) */
  confirmDocument: (id: number, o?: Opts) =>
    post<KbDocument>(`/kb/documents/${id}/confirm`, {}, () => mock.confirmAnswer(id), o).then(normDoc),
  confirmAnswer: (id: number, answer?: string, o?: Opts) =>
    post<KbDocument>(`/kb/answers/${id}/confirm`, answer ? { answer } : {}, () => mock.confirmAnswer(id, answer), o).then(normDoc),
  importScenario: (file: File, o?: Opts & { onProgress?: (p: number) => void }) => {
    const form = new FormData()
    form.append('file', file)
    return upload<ScenarioReport>('/scenario/import', form, () => mock.importScenario(), o).then(normReport)
  },

  topics: () => get<Topic[]>('/topics', () => mock.topics()),
  analytics: (site?: SiteCode | null) =>
    get<Analytics>(`/analytics${siteQuery(site)}`, () => mock.analytics(site)).then(normAnalytics),
  risks: (site?: SiteCode | null) =>
    get<RiskItem[]>(`/analytics/risks${siteQuery(site)}`, () => mock.risks(site)).then(normRisks),
  shift: (to: ShiftTo, employeeId?: number, o?: Opts) =>
    post<ShiftResult>('/demo/shift', employeeId ? { to, employee_id: employeeId } : { to }, () => mock.shift(to, employeeId), o),
  report: (o?: Opts) => post<ReportResult>('/demo/report', {}, () => mock.report(), o),
}
