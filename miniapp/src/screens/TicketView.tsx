import { useEffect, useRef, useState } from 'react'
import { ArrowUp, BookOpen, Bot, Check, ShieldAlert, UserCheck } from 'lucide-react'
import { api } from '../api/client'
import { SITES } from '../api/mocks'
import type { SiteCode, Ticket } from '../api/types'
import { Chip, Screen, SkeletonList, Switch, fmtDate, fmtTime, useAsync, useToast } from '../components/ui'
import { haptic } from '../lib/bridge'
import { useNav } from '../lib/nav'
import { CategoryIcon } from './Tickets'

const STATUS_LABEL = { open: 'Открыто', in_progress: 'В работе', closed: 'Закрыто' } as const

export default function TicketView({ id }: { id: number }) {
  const nav = useNav()
  const toast = useToast()
  const { data: t, setData, loading } = useAsync(() => api.ticket(id), [id])
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

  const run = async (fn: () => Promise<Ticket>, ok: string) => {
    setBusy(true)
    try {
      const next = await fn()
      setData(next)
      nav.refreshBadge()
      toast('success', ok)
    } catch {
      toast('error', 'Не получилось. Попробуйте ещё раз')
    } finally {
      setBusy(false)
    }
  }

  const send = () => {
    const v = text.trim()
    if (!v || !t) return
    haptic.tap()
    setText('')
    run(() => api.reply(t.id, { text: v, add_to_kb: toKb, kb_site: toKb ? kbSite : null }), toKb ? 'Ответ отправлен и добавлен в базу' : 'Ответ отправлен в чат новичку')
    setToKb(false)
  }

  const composer = t && t.status !== 'closed' && (
    <div className="composer">
      <div className="composer-inner">
        <div className="composer-actions">
          {t.status === 'open' && (
            <button className="btn secondary small" disabled={busy} onClick={() => run(() => api.takeTicket(t.id), 'Взяли в работу')}>
              <UserCheck size={18} /> Взять
            </button>
          )}
          <button className="btn secondary small" disabled={busy} onClick={() => run(() => api.closeTicket(t.id), 'Обращение закрыто')}>
            <Check size={18} /> Закрыть
          </button>
        </div>
        <div className="composer-kb">
          <BookOpen size={18} color="var(--text-secondary)" />
          <label>{toKb ? 'Ответ в базу для' : 'Добавить ответ в базу'}</label>
          {toKb && (
            <select aria-label="Площадка для ответа" value={kbSite} onChange={(e) => setKbSite(e.target.value as SiteCode | 'all')}>
              <option value="all">Все площадки</option>
              {SITES.map((s) => (
                <option key={s.code} value={s.code}>{s.name}</option>
              ))}
            </select>
          )}
          <Switch checked={toKb} onChange={setToKb} label="Добавить ответ в базу" />
        </div>
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
      bottom={composer}
      bottomPad={t && t.status !== 'closed' ? 200 : 24}
    >
      {loading && !t && <SkeletonList rows={2} />}
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
              {t.employee_name} · {t.site} · день {t.day}
              {t.assignee && <> · отвечает {t.assignee}</>}
            </div>
            {t.red_flag && (
              <div className="flag-banner">
                <ShieldAlert />
                <span>Красный флаг. Видит только HR — руководителю не передаётся. Свяжитесь с новичком в течение часа.</span>
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
          {t.status === 'closed' && <p className="footnote">Обращение закрыто. Новичок получил уведомление.</p>}
        </>
      )}
    </Screen>
  )
}
