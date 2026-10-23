import { useState } from 'react'
import { CheckCheck, CircleHelp, Clock, Inbox, MessageSquare, PhoneCall, ShieldAlert } from 'lucide-react'
import { api } from '../api/client'
import type { TicketShort, TicketStatus } from '../api/types'
import { Empty, ErrorState, Screen, Segmented, SkeletonList, fmtAgo, fmtDue, useAsync } from '../components/ui'
import { haptic } from '../lib/bridge'
import { useNav } from '../lib/nav'
import { useSites } from '../lib/sites'

export function CategoryIcon({ t }: { t: TicketShort }) {
  if (t.red_flag) return <ShieldAlert />
  if (t.category === 'unknown') return <CircleHelp />
  if (t.category === 'contact_request') return <PhoneCall />
  return <MessageSquare />
}

function TicketRow({ t, onClick }: { t: TicketShort; onClick: () => void }) {
  const { name } = useSites()
  const due = t.due_at && t.status !== 'closed' ? fmtDue(t.due_at) : null
  return (
    <button className="cell ticket-cell" onClick={() => { haptic.tap(); onClick() }}>
      <div className="cell-body" style={{ gap: 4 }}>
        <div className="ticket-head">
          <span className={`ticket-cat${t.red_flag ? ' danger' : ''}`}>
            <CategoryIcon t={t} />
            <span className="clamp-1">{t.category_label}</span>
            {t.red_flag && <span className="chip danger">Красный флаг</span>}
          </span>
          {due ? (
            <span className={`ticket-time num${due.overdue ? ' overdue' : ''}`}>
              <Clock size={13} />
              {due.text}
            </span>
          ) : (
            <span className="ticket-time">{fmtAgo(t.created_at)}</span>
          )}
        </div>
        {t.preview && <div className="ticket-quote clamp-2">«{t.preview}»</div>}
        <div className="cell-meta num clamp-2">
          {t.employee_name} · {name(t.site)} · день {t.day}
        </div>
      </div>
    </button>
  )
}

const EMPTY: Record<TicketStatus, { title: string; text: string }> = {
  open: { title: 'Новых обращений нет', text: 'Когда бот не найдёт ответ или заметит тревожный ответ в опросе, обращение появится здесь.' },
  in_progress: { title: 'В работе пусто', text: 'Обращение попадает сюда, когда вы берёте его или отвечаете новичку.' },
  closed: { title: 'Закрытых пока нет', text: 'Закрытые обращения хранятся здесь, к ним можно вернуться.' },
}

export default function Tickets() {
  const nav = useNav()
  const [status, setStatus] = useState<TicketStatus>('open')
  const { data, demo, error, loading, reload, showSkeleton } = useAsync(() => api.tickets(status), [status])

  return (
    <Screen title="Обращения" large demo={demo}>
      <Segmented
        ariaLabel="Статус обращений"
        value={status}
        onChange={setStatus}
        options={[
          { value: 'open', label: 'Открытые' },
          { value: 'in_progress', label: 'В работе' },
          { value: 'closed', label: 'Закрытые' },
        ]}
      />
      {showSkeleton && <SkeletonList rows={4} />}
      {error && <ErrorState error={error} onRetry={reload} />}
      {data && data.length === 0 && (
        <Empty icon={status === 'closed' ? <CheckCheck /> : <Inbox />} title={EMPTY[status].title} text={EMPTY[status].text} />
      )}
      {data && data.length > 0 && (
        <section className="section" style={{ opacity: loading ? 0.6 : 1, transition: 'opacity 150ms ease' }}>
          <div className="group">
            {data.map((t) => (
              <TicketRow key={t.id} t={t} onClick={() => nav.push({ name: 'ticket', id: t.id })} />
            ))}
          </div>
          <div className="section-footer">Красные флаги и ответы опросов видит только HR, руководителю они не приходят.</div>
        </section>
      )}
    </Screen>
  )
}
