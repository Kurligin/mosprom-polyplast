import { useEffect, useRef, useState } from 'react'
import { ArrowUp, BookOpen, Bot, Check, ShieldAlert, UserCheck } from 'lucide-react'
import { api, toApiError } from '../api/client'
import type { SiteCode, Ticket } from '../api/types'
import { Chip, ErrorState, Screen, SkeletonList, Switch, fmtDate, fmtTime, useAsync, useToast } from '../components/ui'
import { haptic } from '../lib/bridge'
import { useNav } from '../lib/nav'
import { useSites } from '../lib/sites'
import { CategoryIcon } from './Tickets'

const STATUS_LABEL = { open: 'Открыто', in_progress: 'В работе', closed: 'Закрыто' } as const

/** Что сказать после ответа: ушёл ли он в «МАКС» и создан ли черновик в базе (по ответу сервера) */
function replyResult(t: Ticket): ['success' | 'warning', string] {
  const kb = t.added_to_kb ? ' Черновик ждёт подтверждения в базе.' : ''
  // delivered: false — ответ сохранён в ленте, но в «МАКС» не ушёл
  if (t.delivered === false && t.synthetic) return ['success', `Ответ сохранён в ленте. Новичок синтетический, в «МАКС» ему ничего не уходит.${kb}`]
  if (t.delivered === false) return ['warning', `Ответ сохранён, но новичок ещё не подключился к боту. Свяжитесь с ним сами.${kb}`]
  return ['success', t.added_to_kb ? 'Ответ отправлен. Черновик ждёт подтверждения в базе' : 'Ответ отправлен новичку в чат']
}

export default function TicketView({ id }: { id: number }) {
  const nav = useNav()
  const toast = useToast()
  const { data: t, setData, demo, error, reload, showSkeleton } = useAsync(() => api.ticket(id), [id])
  const { sites, name: siteName } = useSites()
  const [text, setText] = useState('')
  const [toKb, setToKb] = useState(false)
  const [kbSite, setKbSite] = useState<SiteCode | 'all'>('all')
  const [busy, setBusy] = useState(false)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (t) setKbSite(t.site)
  }, [t?.site]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    const sc = endRef.current?.closest('.screen')
    if (sc) sc.scrollTop = sc.scrollHeight
  }, [t?.messages.length])

  const run = async (fn: () => Promise<Ticket>, ok: string | ((t: Ticket) => ['success' | 'warning', string]), onFail?: () => void) => {
    setBusy(true)
    try {
      const next = await fn()
      setData(next)
      nav.refreshBadge()
      const [kind, text] = typeof ok === 'string' ? (['success', ok] as const) : ok(next)
      toast(kind, text)
      return true
    } catch (e) {
      toast('error', toApiError(e).message)
      onFail?.()
      return false
    } finally {
      setBusy(false)
    }
  }

  const send = () => {
    const v = text.trim()
    if (!v || !t) return
    haptic.tap()
    const kb = toKb && !t.is_personal
    setText('')
    setToKb(false)
    run(
      () => api.reply(t.id, { text: v, add_to_kb: kb, kb_site: kb ? kbSite : null }, { demo }),
      replyResult,
      // не теряем текст, если отправить не получилось
      () => { setText(v); setToKb(kb) },
    )
  }

  const composer = t && t.status !== 'closed' && (
    <div className="composer">
      <div className="composer-inner">
        <div className="composer-actions">
          {t.status === 'open' && (
            <button className="btn secondary small" disabled={busy} onClick={() => run(() => api.takeTicket(t.id, { demo }), 'Взяли в работу')}>
              <UserCheck size={18} /> Взять
            </button>
          )}
          <button className="btn secondary small" disabled={busy} onClick={() => run(() => api.closeTicket(t.id, { demo }), 'Обращение закрыто')}>
            <Check size={18} /> Закрыть
          </button>
        </div>
        {/* личный вопрос в базу не добавляется: переключатель не показываем */}
        {!t.is_personal && <div className="composer-kb">
          <BookOpen size={18} color="var(--text-secondary)" />
          <label>{toKb ? 'Ответ в базу для' : 'Добавить ответ в базу'}</label>
          {toKb && (
            <select aria-label="Площадка для ответа" value={kbSite} onChange={(e) => setKbSite(e.target.value as SiteCode | 'all')}>
              <option value="all">Все площадки</option>
              {sites.map((s) => (
                <option key={s.code} value={s.code}>{s.name}</option>
              ))}
            </select>
          )}
          <Switch checked={toKb} onChange={setToKb} label="Добавить ответ в базу" />
        </div>}
        <div className="composer-input">
          <textarea
            rows={1}
            placeholder="Ответ новичку"
            value={text}
            onChange={(e) => setText(e.target.value)}
            enterKeyHint="send"
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey) {
                e.preventDefault()
                send()
              }
            }}
          />
          <button className="send-btn" aria-label="Отправить" disabled={!text.trim() || busy} onClick={send}>
            <ArrowUp />
          </button>
        </div>
      </div>
    </div>
  )

  let lastDay = ''
  return (
    <Screen
      title={t?.category_label ?? 'Обращение'}
      back={{ label: nav.backLabel, onClick: nav.pop }}
      noTabbar
      demo={demo}
      bottom={composer}
      bottomPad={t && t.status !== 'closed' ? 200 : 24}
    >
      {showSkeleton && <SkeletonList rows={2} />}
      {error && <ErrorState error={error} onRetry={reload} title={error.status === 404 ? 'Обращение не найдено' : undefined} />}
      {t && (
        <>
          <div className="ticket-info">
            <div className={`ticket-cat${t.red_flag ? ' danger' : ''}`} style={{ fontSize: 17 }}>
              <CategoryIcon t={t} />
              {t.category_label}
              <span style={{ flex: 1 }} />
              <Chip tone={t.status === 'open' ? 'warning' : t.status === 'in_progress' ? 'primary' : 'success'}>{STATUS_LABEL[t.status]}</Chip>
            </div>
            <div className="ticket-info-row num">
              {t.employee_name} · {siteName(t.site)} · день {t.day}
              {t.assignee && <> · отвечает {t.assignee}</>}
            </div>
            {t.red_flag && (
              <div className="flag-banner">
                <ShieldAlert />
                <span>Красный флаг. Его видит только HR, руководителю он не уходит. Свяжитесь с новичком в течение часа.</span>
              </div>
            )}
          </div>

          <div className="chat">
            {t.messages.map((m) => {
              const d = fmtDate(m.created_at)
              const showDay = d !== lastDay
              lastDay = d
              const mine = m.author === 'hr'
              return (
                <div key={m.id} style={{ display: 'contents' }}>
                  {showDay && <div className="chat-day">{d}</div>}
                  <div className={`bubble ${mine ? 'mine' : 'in'} ${m.author === 'bot' ? 'bot' : ''}`}>
                    {m.author !== 'employee' && (
                      <div className="bubble-author">
                        {m.author === 'bot' && <Bot size={13} />}
                        {m.author_name}
                      </div>
                    )}
                    {m.text}
                    {m.source && <div className="bubble-source">Источник: {m.source}</div>}
                    <span className="bubble-time num">{fmtTime(m.created_at)}</span>
                  </div>
                </div>
              )
            })}
            <div ref={endRef} />
          </div>
          {t.status === 'closed' && <p className="footnote">Обращение закрыто.</p>}
        </>
      )}
    </Screen>
  )
}
