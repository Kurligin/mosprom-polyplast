import { initDataHeader, isInMax } from '../lib/bridge'
import { ApiError, FORCE_MOCKS, roleParam } from './client'
import { MOCK_MODE } from './mocks'
import type { SiteCode, WorkType } from './types'

/*
 * Чаты команды (miniapp/API.md, раздел «Чаты команды»): групповые чаты «МАКС», куда добавлен Корсик.
 * HR отмечает, кому нужен чат; в первый рабочий день бот сам добавляет туда новичка.
 * Свой маленький клиент: в общем client.ts нет PATCH. Демо-данные — по тем же правилам:
 * сервер недоступен или эндпоинт не готов (501, маршрута нет), либо ?mocks=1.
 */

/** Кому нужен чат: никому (пока HR не настроил), всем новичкам, новичкам выбранных площадок */
export type ChatAudience = 'none' | 'all' | 'sites'

export interface TeamChat {
  id: number
  title: string
  /** Ссылка-приглашение: её получит новичок, если «МАКС» не дал добавить его в чат */
  link: string | null
  audience: ChatAudience
  sites: SiteCode[]
  /** null — и офису, и производству */
  work_type: WorkType | null
  /** Сюда бот публикует рассказ новичка о себе (с его согласия) */
  for_intro: boolean
  is_hr_group: boolean
  /** false — бота удалили из чата */
  active: boolean
  members_count: number | null
  /** Сколько новичков бот добавил в чат */
  added: number
  /** Скольким новичкам отправил ссылку вместо добавления */
  links_sent: number
  created_at: string | null
}

/** Только изменённые поля. work_type: null или 'all' — и офису, и производству */
export type TeamChatPatch = Partial<{
  audience: ChatAudience
  sites: SiteCode[]
  work_type: WorkType | 'all' | null
  for_intro: boolean
  is_hr_group: boolean
}>

/** Чат в карточке новичка: поле `chats` у Employee */
export type EmployeeChatStatus = 'added' | 'link' | 'failed' | 'pending' | 'intro'

export interface EmployeeChat {
  chat_id: number
  title: string
  status: EmployeeChatStatus
  at: string | null
  /** Почему не добавили: «не пустили настройки приватности» */
  reason: string | null
  link: string | null
  /** В этом чате опубликован рассказ новичка о себе */
  intro: boolean
}

export const EMPLOYEE_CHAT_STATUS: Record<EmployeeChatStatus, string> = {
  added: 'Добавлен',
  link: 'Отправили ссылку',
  failed: 'Не получилось добавить',
  pending: 'Добавим в первый рабочий день',
  intro: 'Опубликовали представление',
}

export const AUDIENCE_LABEL: Record<ChatAudience, string> = {
  none: 'Никому',
  all: 'Всем новичкам',
  sites: 'Площадкам',
}

export const WORK_TYPE_LABEL: Record<WorkType, string> = { office: 'Офис', production: 'Производство' }

/** Кому нужен чат, одной строкой: «Всем новичкам · Производство», «GTP, GrPP · Офис» */
export function audienceText(c: Pick<TeamChat, 'audience' | 'sites' | 'work_type'>, siteName: (code: SiteCode) => string = (s) => s) {
  if (c.audience === 'none') return 'Новичков сюда не добавляем'
  const who = c.audience === 'all' ? 'Всем новичкам' : c.sites.length ? c.sites.map(siteName).join(', ') : 'Площадки не выбраны'
  return c.work_type ? `${who} · ${WORK_TYPE_LABEL[c.work_type]}` : who
}

/** Мой список: данные и признак демо (общий markDemo из client.ts сюда не дотягивается) */
export type ChatsList = { chats: TeamChat[]; demo: boolean }

/* ---------- Запросы ---------- */

type Outcome =
  | { kind: 'ok'; data: unknown }
  | { kind: 'fallback' } // сеть, 5xx, не JSON, 501, маршрута нет
  | { kind: 'error'; error: ApiError }

const STATUS_TEXT: Record<number, string> = {
  400: 'Сервер не принял изменения',
  401: 'Откройте панель из чата с Корсиком в «МАКС»',
  403: 'Чаты настраивает HR',
  404: 'Чат не найден. Возможно, бота уже удалили',
  409: 'Бота удалили из этого чата',
  422: 'Сервер не принял изменения',
}

function headers(json: boolean): Record<string, string> {
  const h: Record<string, string> = { 'X-Max-Init-Data': initDataHeader(), Accept: 'application/json' }
  if (!isInMax() && (roleParam === 'manager' || roleParam === 'head' || roleParam === 'hr')) h['X-Dev-Role'] = roleParam
  if (json) h['Content-Type'] = 'application/json'
  return h
}

async function request(method: 'GET' | 'PATCH', path: string, body?: unknown): Promise<Outcome> {
  let res: Response
  try {
    res = await fetch(`/api${path}`, {
      method,
      headers: headers(body !== undefined),
      body: body !== undefined ? JSON.stringify(body) : undefined,
    })
  } catch {
    return { kind: 'fallback' }
  }
  let parsed: unknown = null
  if ((res.headers.get('content-type') ?? '').includes('application/json')) {
    try {
      parsed = await res.json()
    } catch {
      return { kind: 'fallback' }
    }
  } else if (res.ok) {
    return { kind: 'fallback' }
  }
  const raw = (parsed as { detail?: unknown } | null)?.detail
  const detail = typeof raw === 'string' ? raw : Array.isArray(raw) && typeof raw[0]?.msg === 'string' ? (raw[0].msg as string) : null
  if (res.ok) return { kind: 'ok', data: parsed }
  if (res.status === 501 || res.status === 405 || res.status >= 500 || (res.status === 404 && detail === 'Not Found')) {
    return { kind: 'fallback' }
  }
  return { kind: 'error', error: new ApiError(res.status, detail ?? STATUS_TEXT[res.status] ?? 'Не получилось. Попробуйте ещё раз') }
}

/* ---------- Демо-данные (синтетика) ---------- */

let demoChats: TeamChat[] | null = null

function demoList(): TeamChat[] {
  if (!demoChats) {
    const base = { link: null, sites: [], work_type: null, for_intro: false, is_hr_group: false, active: true, added: 0, links_sent: 0, created_at: null }
    // стресс-режимы разработки (?mocks=worst | empty | many), см. mocks.ts
    if (MOCK_MODE === 'empty') return (demoChats = [])
    if (MOCK_MODE === 'many') {
      return (demoChats = Array.from({ length: 300 }, (_, i): TeamChat => ({
        ...base, id: -5000 - i, title: `Чат смены ${i + 1}`, audience: (['none', 'all', 'sites'] as const)[i % 3],
        sites: i % 3 === 2 ? ['GTP'] : [], is_hr_group: i === 0, active: i % 17 !== 0, members_count: 10 + i, added: i % 9,
        link: i % 4 ? `https://max.ru/join/chat-${i}` : null,
      })))
    }
    if (MOCK_MODE === 'worst') {
      return (demoChats = [
        { ...base, id: -900, title: 'HR ПОЛИПЛАСТИК: адаптация, обращения новичков и красные флаги (только для HR, без руководителей) 🔒', audience: 'none', is_hr_group: true, members_count: null },
        { ...base, id: -910, title: 'Команда производства GTP: экструзия, грануляция и упаковка 🏭 (смены А, Б, В)', audience: 'sites', sites: ['GTP', 'Luzhniki', 'GrPP', 'KZN-2', 'Kazan_Zeleny_Dol_promploshchadka_2'], work_type: 'production', for_intro: true, members_count: 12480, added: 1284, links_sent: 312 },
        { ...base, id: -920, title: 'polyplastic_gtp_extrusion_line3_night_shift_2026', audience: 'all', link: 'https://max.ru/join/AbCdEfGhIjKlMnOpQrStUvWxYz0123456789', members_count: 1, added: 1, links_sent: 0 },
        { ...base, id: -930, title: '🍲', audience: 'sites', sites: [], members_count: 0 },
        { ...base, id: -940, title: 'Ю', audience: 'none', members_count: null },
        { ...base, id: -950, title: 'Старый чат участка экструзии, из которого Корсика удалили в прошлом году', audience: 'sites', sites: ['GrPP'], active: false, members_count: 52 },
      ])
    }
    demoChats = [
      { ...base, id: -900, title: 'HR ПОЛИПЛАСТИК (демо)', audience: 'none', is_hr_group: true, members_count: 6 },
      { ...base, id: -910, title: 'Общий чат ПОЛИПЛАСТИК (демо)', audience: 'all', link: 'https://max.ru/join/demo-general', members_count: 240, added: 14, links_sent: 2 },
      { ...base, id: -920, title: 'Команда GTP (демо)', audience: 'sites', sites: ['GTP'], work_type: 'production', for_intro: true, link: 'https://max.ru/join/demo-gtp', members_count: 35, added: 5 },
      { ...base, id: -930, title: 'Офис GrPP (демо)', audience: 'sites', sites: ['GrPP'], work_type: 'office', members_count: 18, added: 3, links_sent: 1 },
      { ...base, id: -940, title: 'Курилка (демо)', audience: 'none', members_count: 52 },
    ]
  }
  return demoChats
}

/** Правка чата так, как её применит сервер: для мгновенного отклика экрана и для демо */
export function applyPatch(chat: TeamChat, patch: TeamChatPatch): TeamChat {
  const next: TeamChat = { ...chat }
  if (patch.audience) next.audience = patch.audience
  if (patch.sites) next.sites = [...new Set(patch.sites)]
  if ('work_type' in patch) next.work_type = patch.work_type === 'all' ? null : (patch.work_type ?? null)
  if (typeof patch.for_intro === 'boolean') next.for_intro = patch.for_intro
  if (patch.is_hr_group === true) Object.assign(next, { is_hr_group: true, audience: 'none', sites: [], work_type: null, for_intro: false })
  if (patch.is_hr_group === false) next.is_hr_group = false
  return next
}

function demoPatch(id: number, patch: TeamChatPatch): TeamChat {
  const chat = demoList().find((c) => c.id === id)
  if (!chat) throw new ApiError(404, STATUS_TEXT[404])
  const next = applyPatch(chat, patch)
  demoChats = demoList().map((c) => (c.id === id ? next : patch.is_hr_group ? { ...c, is_hr_group: false } : c))
  return next
}

const wait = (ms: number) => new Promise((r) => setTimeout(r, ms))

function normChat(raw: Partial<TeamChat>): TeamChat {
  return {
    id: Number(raw.id),
    title: raw.title || 'Чат без названия',
    link: raw.link ?? null,
    audience: raw.audience === 'all' || raw.audience === 'sites' ? raw.audience : 'none',
    sites: Array.isArray(raw.sites) ? raw.sites : [],
    work_type: raw.work_type === 'office' || raw.work_type === 'production' ? raw.work_type : null,
    for_intro: !!raw.for_intro,
    is_hr_group: !!raw.is_hr_group,
    active: raw.active !== false,
    members_count: typeof raw.members_count === 'number' ? raw.members_count : null,
    added: raw.added ?? 0,
    links_sent: raw.links_sent ?? 0,
    created_at: raw.created_at ?? null,
  }
}

export const chatsApi = {
  /** GET /api/chats — HR-группа первой, чаты без бота в конце */
  list: async (): Promise<ChatsList> => {
    if (!FORCE_MOCKS) {
      const r = await request('GET', '/chats')
      if (r.kind === 'ok') return { chats: (Array.isArray(r.data) ? r.data : []).map(normChat), demo: false }
      if (r.kind === 'error') throw r.error
    }
    await wait(160)
    return { chats: demoList().map((c) => ({ ...c })), demo: true }
  },

  /** PATCH /api/chats/{id}. demo — экран на демо-данных: меняем только их */
  update: async (id: number, patch: TeamChatPatch, o?: { demo?: boolean }): Promise<TeamChat> => {
    if (FORCE_MOCKS || o?.demo) {
      await wait(160)
      return demoPatch(id, patch)
    }
    const r = await request('PATCH', `/chats/${id}`, patch)
    if (r.kind === 'ok') return normChat(r.data as Partial<TeamChat>)
    if (r.kind === 'error') throw r.error
    throw new ApiError(0, 'Сервер не отвечает. Проверьте связь и попробуйте ещё раз')
  },
}
