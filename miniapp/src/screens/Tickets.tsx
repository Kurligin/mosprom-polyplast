import { useState } from 'react'
import { CircleHelp, Clock, Inbox, MessageSquare, PhoneCall, ShieldAlert } from 'lucide-react'
import { api } from '../api/client'
import type { TicketShort, TicketStatus } from '../api/types'
import { Empty, Screen, Segmented, SkeletonList, fmtAgo, fmtDue, useAsync } from '../components/ui'
import { haptic } from '../lib/bridge'
import { useNav } from '../lib/nav'

export function CategoryIcon({ t }: { t: TicketShort }) {
  if (t.red_flag) return <ShieldAlert />
  if (t.category === 'unknown') return <CircleHelp />
  if (t.category === 'contact_request') return <PhoneCall />
  return <MessageSquare />
}

function TicketRow({ t, onClick }: { t: TicketShort; onClick: () => void }) {
  const due = t.due_at && t.status !== 'closed' ? fmtDue(t.due_at) : null
  return (
    <button className="cell ticket-cell" onClick={() => { haptic.tap(); onClick() }}>
      <div className="cell-body" style={{ gap: 4 }}>
        <div className="ticket-head">
          <span className={`ticket-cat${t.red_flag ? ' danger' : ''}`}>
            <CategoryIcon t={t} />
            {t.category_label}
            {t.red_flag && <span className="chip danger" style={{ marginLeft: 2 }}>Красный флаг</span>}
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
        <div className="ticket-quote clamp-1">«{t.preview}»</div>
        <div className="cell-meta num">
          {t.employee_name} · {t.site} · день {t.day}
        </div>
      </div>
    </button>
  )
}

export default function Tickets() {
  const nav = useNav()
  const [status, setStatus] = useState<TicketStatus>('open')
  const { data, loading } = useAsync(() => api.tickets(status), [status])

  return (
    <Screen title="Обращения" large>
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
      {loading && !data && <SkeletonList rows={4} />}
      {data && data.length === 0 && (
        <Empty
          icon={<Inbox />}
          title={status === 'open' ? 'Новых обращений нет' : status === 'in_progress' ? 'В работе пусто' : 'Закрытых пока нет'}
          text={status === 'open' ? 'Когда бот не найдёт ответ или заметит красный флаг, обращение появится здесь.' : undefined}
        />
      )}
      {data && data.length > 0 && (
        <section className="section" style={{ opacity: loading ? 0.6 : 1, transition: 'opacity 150ms ease' }}>
          <div className="group">
            {data.map((t) => (
              <TicketRow key={t.id} t={t} onClick={() => nav.push({ name: 'ticket', id: t.id })} />
            ))}
          </div>
          <div className="section-footer">Красные флаги видит только HR. Руководитель не получает ответы опросов и обращения.</div>
        </section>
      )}
    </Screen>
  )
}
