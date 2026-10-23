import { initData } from '../lib/bridge'
import { mock } from './mocks'
import type {
  Employee, EmployeeShort, KbDocument, Me, NewEmployeeInput, NewEmployeeResult, ReplyInput, ReportResult,
  ShiftResult, ShiftTo, Ticket, TicketShort, TicketStatus, Topic,
} from './types'

const FORCE_MOCKS = import.meta.env.VITE_USE_MOCKS === '1'

let demo = FORCE_MOCKS
const listeners = new Set<(v: boolean) => void>()
export const isDemo = () => demo
export function onDemoChange(cb: (v: boolean) => void) {
  listeners.add(cb)
  return () => {
    listeners.delete(cb)
  }
}
function setDemo(v: boolean) {
  if (demo === v) return
  demo = v
  listeners.forEach((cb) => cb(v))
}

const params = new URLSearchParams(location.search)
export const roleParam = params.get('role')

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

async function request<T>(method: 'GET' | 'POST', path: string, body: unknown, fallback: () => T): Promise<T> {
  if (!FORCE_MOCKS) {
    try {
      const res = await fetch(`/api${path}`, {
        method,
        headers: {
          'X-Max-Init-Data': initData(),
          ...(body !== undefined ? { 'Content-Type': 'application/json' } : {}),
        },
        body: body !== undefined ? JSON.stringify(body) : undefined,
      })
      const ct = res.headers.get('content-type') ?? ''
      if (res.ok && ct.includes('application/json')) {
        setDemo(false)
        return (await res.json()) as T
      }
    } catch {
      /* сеть / бэкенд не поднят — падаем в демо-данные */
    }
  }
  setDemo(true)
  await wait(120)
  return fallback()
}

export const api = {
  me: () =>
    request<Me>('GET', '/me', undefined, () => mock.me(roleParam)).then((me): Me =>
      roleParam === 'manager' || roleParam === 'hr' ? { ...me, role: roleParam } : me,
    ),
  employees: (opts: { mine?: boolean } = {}) =>
    request<EmployeeShort[]>('GET', `/employees${opts.mine ? '?mine=1' : ''}`, undefined, () => mock.employees(opts.mine)),
  employee: (id: number) => request<Employee>('GET', `/employees/${id}`, undefined, () => mock.employee(id)),
  createEmployee: (input: NewEmployeeInput) =>
    request<NewEmployeeResult>('POST', '/employees', input, () => mock.createEmployee(input)),
  tickets: (status?: TicketStatus) =>
    request<TicketShort[]>('GET', `/tickets${status ? `?status=${status}` : ''}`, undefined, () => mock.tickets(status)),
  ticket: (id: number) => request<Ticket>('GET', `/tickets/${id}`, undefined, () => mock.ticket(id)),
  reply: (id: number, input: ReplyInput) =>
    request<Ticket>('POST', `/tickets/${id}/reply`, input, () => mock.reply(id, input)),
  takeTicket: (id: number) => request<Ticket>('POST', `/tickets/${id}/take`, {}, () => mock.setStatus(id, 'in_progress')),
  closeTicket: (id: number) => request<Ticket>('POST', `/tickets/${id}/close`, {}, () => mock.setStatus(id, 'closed')),
  documents: () => request<KbDocument[]>('GET', '/kb/documents', undefined, () => mock.documents()),
  topics: () => request<Topic[]>('GET', '/topics', undefined, () => mock.topics()),
  shift: (to: ShiftTo) => request<ShiftResult>('POST', '/demo/shift', { to }, () => mock.shift(to)),
  report: () => request<ReportResult>('POST', '/demo/report', {}, () => mock.report()),
  meetingDone: (employeeId: number, meetingId: number) =>
    request<Employee>('POST', `/meetings/${meetingId}/done`, {}, () => mock.meetingDone(employeeId, meetingId)),
}
