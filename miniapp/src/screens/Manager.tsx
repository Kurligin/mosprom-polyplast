import { useState } from 'react'
import { CalendarCheck, CalendarClock, Check, ClipboardCheck, Users } from 'lucide-react'
import { api, roleParam, toApiError } from '../api/client'
import type { Employee, EmployeeShort } from '../api/types'
import {
  Avatar, Cell, Empty, ErrorState, Progress, Screen, Section, SkeletonCard, SkeletonList, dayLabel, employeeToday, fmtDate,
  useAsync, useToast,
} from '../components/ui'
import { haptic } from '../lib/bridge'
import { useNav } from '../lib/nav'
import { IntroSection, PlanSection, ProbationHeader } from './EmployeeCard'

/**
 * Пора встретиться: дата встречи уже наступила у новичка. Сравниваем даты, а не номер встречи с днём адаптации:
 * встреча «3-го дня» — на 3-й рабочий день, а день адаптации календарный.
 */
const meetingDue = (e: EmployeeShort) => !!e.next_meeting && e.next_meeting.date <= employeeToday(e)

export function ManagerHome() {
  const nav = useNav()
  const { data: raw, demo, error, reload, showSkeleton } = useAsync(() => api.employees({ mine: true }), [])
  // сначала те, с кем пора встретиться, потом по дате ближайшей встречи
  const data = raw
    ? [...raw].sort((a, b) => {
        const ao = meetingDue(a) ? 0 : 1
        const bo = meetingDue(b) ? 0 : 1
        if (ao !== bo) return ao - bo
        return (a.next_meeting?.date ?? '9999').localeCompare(b.next_meeting?.date ?? '9999')
      })
    : null
  return (
    <Screen title="Мои новички" large noTabbar demo={demo}>
      {showSkeleton && <SkeletonList rows={3} />}
      {error && <ErrorState error={error} onRetry={reload} />}
      {data && data.length === 0 && (
        <Empty
          icon={<Users />}
          title="Новичков пока нет"
          text="Когда HR заведёт сотрудника в вашу команду, он появится здесь. Корсик напомнит о встречах на 3‑й, 30‑й и 60‑й день."
        />
      )}
      {data && data.length > 0 && (
        <section className="section">
          <div className="group">
            {data.map((e) => {
              const overdue = meetingDue(e)
              return (
                <button
                  key={e.id}
                  className="cell"
                  style={{ ['--sep-inset' as string]: '68px', alignItems: 'flex-start' }}
                  onClick={() => { haptic.tap(); nav.push({ name: 'manager-employee', id: e.id }) }}
                >
                  <Avatar name={e.full_name} />
                  <div className="cell-body">
                    <div className="cell-title strong clamp-2">{e.full_name}</div>
                    <div className="cell-meta num clamp-1">{dayLabel(e.day, e.probation_days)}{e.position ? ` · ${e.position}` : ''}</div>
                    <Progress value={e.day / e.probation_days} label="Испытательный срок" />
                    {e.next_meeting && (
                      <div className="chips">
                        <span className={`chip ${overdue ? 'warning' : 'primary'}`}>
                          <CalendarClock />
                          {overdue ? 'пора встретиться' : e.next_meeting.title.toLowerCase()} · {fmtDate(e.next_meeting.date)}
                        </span>
                      </div>
                    )}
                  </div>
                </button>
              )
            })}
          </div>
          <div className="section-footer">Ответы опросов и обращения новичков видит только HR.</div>
        </section>
      )}
      {roleParam === 'manager' && (
        <div className="stack-actions">
          <button
            className="btn plain small"
            onClick={() => {
              const q = new URLSearchParams(location.search)
              q.delete('role')
              location.search = q.toString()
            }}
          >
            Вернуться в режим HR
          </button>
        </div>
      )}
    </Screen>
  )
}

export function ManagerEmployee({ id }: { id: number }) {
  const nav = useNav()
  const toast = useToast()
  const { data: e, setData, demo, error, reload, showSkeleton } = useAsync(() => api.employee(id), [id])
  const [busy, setBusy] = useState(false)
  const next = e?.meetings.find((m) => !m.done) ?? null

  const done = async () => {
    if (!e || !next) return
    setBusy(true)
    try {
      const r: Employee = await api.meetingDone(e.id, next.id, { demo })
      setData(r)
      toast('success', 'Отметили: встреча проведена')
    } catch (err) {
      toast('error', toApiError(err).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Screen title={e?.full_name ?? 'Новичок'} back={{ label: nav.backLabel, onClick: nav.pop }} noTabbar demo={demo} revealTitleAt={150}>
      {showSkeleton && <SkeletonCard />}
      {error && <ErrorState error={error} onRetry={reload} title={error.status === 404 ? 'Новичок не найден' : undefined} />}
      {e && (
        <>
          <ProbationHeader e={e} hideSignals />
          <Section header="Встречи с новичком">
            {e.meetings.length === 0 && <Cell title="Встречи появятся после выхода на работу" />}
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
                meta={`${fmtDate(m.date)}${m.done ? ' · проведена' : m.id === next?.id && m.date <= employeeToday(e) ? ' · пора встретиться' : ''}`}
              />
            ))}
          </Section>
          {next ? (
            <div style={{ marginTop: 16 }}>
              <button className="btn" onClick={done} disabled={busy}>
                <CalendarCheck size={20} /> {busy ? 'Отмечаем…' : 'Провёл встречу'}
              </button>
              <p className="footnote" style={{ paddingTop: 8 }}>Отметка уйдёт HR. Про следующую встречу Корсик напомнит сам.</p>
            </div>
          ) : e.meetings.length > 0 ? (
            <Empty icon={<ClipboardCheck />} title="Все встречи проведены" text="Итог испытательного срока HR обсудит с вами отдельно." />
          ) : null}

          <PlanSection e={e} demo={demo} onUpdate={setData} />
          <IntroSection e={e} demo={demo} canGate={false} onUpdate={setData} />
        </>
      )}
    </Screen>
  )
}
