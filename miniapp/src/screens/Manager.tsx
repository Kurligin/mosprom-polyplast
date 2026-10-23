import { CalendarCheck, CalendarClock, Check, ClipboardCheck, Users } from 'lucide-react'
import { api } from '../api/client'
import type { Employee } from '../api/types'
import { Avatar, Cell, Empty, Progress, Screen, Section, SkeletonList, dayLabel, fmtDate, useAsync, useToast } from '../components/ui'
import { haptic } from '../lib/bridge'
import { useNav } from '../lib/nav'
import { ProbationHeader } from './EmployeeCard'
import { useState } from 'react'

export function ManagerHome() {
  const nav = useNav()
  const { data, loading } = useAsync(() => api.employees({ mine: true }), [])
  return (
    <Screen title="Мои новички" large noTabbar>
      {loading && !data && <SkeletonList rows={3} />}
      {data && data.length === 0 && (
        <Empty
          icon={<Users />}
          title="Новичков пока нет"
          text="Когда HR заведёт сотрудника в вашу команду, он появится здесь. Корсик напомнит о встречах на 3-й, 30-й и 60-й день."
        />
      )}
      {data && data.length > 0 && (
        <section className="section">
          <div className="group">
            {data.map((e) => (
              <button
                key={e.id}
                className="cell"
                style={{ ['--sep-inset' as string]: '68px', alignItems: 'flex-start' }}
                onClick={() => { haptic.tap(); nav.push({ name: 'manager-employee', id: e.id }) }}
              >
                <Avatar name={e.full_name} />
                <div className="cell-body">
                  <div className="cell-title strong clamp-1">{e.full_name}</div>
                  <div className="cell-meta num">{dayLabel(e.day, e.probation_days)} · {e.position}</div>
                  <Progress value={e.day / e.probation_days} />
                  {e.next_meeting && (
                    <div className="chips">
                      <span className={`chip ${e.next_meeting.day <= e.day ? 'warning' : 'primary'}`}>
                        <CalendarClock />
                        {e.next_meeting.title.toLowerCase()} · {fmtDate(e.next_meeting.date)}
                      </span>
                    </div>
                  )}
                </div>
              </button>
            ))}
          </div>
          <div className="section-footer">Ответы опросов и обращения новичков видит только HR.</div>
        </section>
      )}
    </Screen>
  )
}

export function ManagerEmployee({ id }: { id: number }) {
  const nav = useNav()
  const toast = useToast()
  const { data: e, setData, loading } = useAsync(() => api.employee(id), [id])
  const [busy, setBusy] = useState(false)
  const next = e?.meetings.find((m) => !m.done) ?? null

  const done = async () => {
    if (!e || !next) return
    setBusy(true)
    try {
      const r: Employee = await api.meetingDone(e.id, next.id)
      setData(r)
      toast('success', 'Отметили: встреча проведена')
    } catch {
      toast('error', 'Не получилось отметить')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Screen title={e?.full_name ?? 'Новичок'} back={{ label: nav.backLabel, onClick: nav.pop }} noTabbar>
      {loading && !e && <SkeletonList rows={3} />}
      {e && (
        <>
          <ProbationHeader e={e} hideSignals />
          <Section header="Встречи с новичком">
            {e.meetings.map((m) => (
              <Cell
                key={m.id}
                inset={50}
                left={
                  m.done ? (
                    <span className="step-dot done"><Check /></span>
                  ) : (
                    <span className={`step-dot ${m.id === next?.id ? 'now' : ''}`} />
                  )
                }
                title={m.title}
                meta={`День ${m.day} · ${fmtDate(m.date)}${m.done ? ' · проведена' : ''}`}
              />
            ))}
          </Section>
          {next ? (
            <div style={{ marginTop: 16 }}>
              <button className="btn" onClick={done} disabled={busy}>
                <CalendarCheck size={20} /> Провёл встречу
              </button>
              <p className="footnote" style={{ paddingTop: 8 }}>Отметка уйдёт HR. Следующее напоминание Корсик пришлёт сам.</p>
            </div>
          ) : (
            <Empty icon={<ClipboardCheck />} title="Все встречи проведены" text="Итог испытательного срока HR обсудит с вами отдельно." />
          )}
        </>
      )}
    </Screen>
  )
}
