import { Briefcase, Check, ChevronRight, Factory, MessageSquare, Phone, ShieldAlert } from 'lucide-react'
import { api } from '../api/client'
import type { Employee } from '../api/types'
import { Avatar, Cell, Chip, Empty, Progress, Screen, Section, SkeletonList, dayLabel, fmtAgo, fmtDate, useAsync } from '../components/ui'
import { useNav } from '../lib/nav'
import { EmployeeChips } from './Employees'

export function ProbationHeader({ e, hideSignals }: { e: Employee; hideSignals?: boolean }) {
  const left = Math.max(0, e.probation_days - e.day)
  return (
    <>
      <div className="hero">
        <Avatar name={e.full_name} large />
        <h2>{e.full_name}</h2>
        <p>
          {e.position} · {e.site} ·{' '}
          <span style={{ display: 'inline-flex', alignItems: 'center', gap: 4, verticalAlign: 'middle' }}>
            {e.work_type === 'production' ? <Factory size={15} /> : <Briefcase size={15} />}
            {e.work_type === 'production' ? 'производство' : 'офис'}
          </span>
        </p>
        {!hideSignals && <EmployeeChips e={e} />}
      </div>
      <div className="probation">
        <div className="probation-row">
          <b className="num">{dayLabel(e.day, e.probation_days)}</b>
          <span className="cell-meta num">{e.day >= 1 ? `осталось ${left} дн.` : `выход ${fmtDate(e.start_date)}`}</span>
        </div>
        <Progress value={e.day / e.probation_days} thick />
        <div className="probation-sub">Испытательный срок · вышел(а) {fmtDate(e.start_date)}</div>
      </div>
    </>
  )
}

export default function EmployeeCard({ id }: { id: number }) {
  const nav = useNav()
  const { data: e, loading } = useAsync(() => api.employee(id), [id])

  return (
    <Screen title={e?.full_name ?? 'Новичок'} back={{ label: nav.backLabel, onClick: nav.pop }}>
      {loading && !e && <SkeletonList rows={3} />}
      {e && (
        <>
          <ProbationHeader e={e} />

          <Section header="Ближайшие шаги">
            {e.steps.filter((s) => s.status !== 'done').slice(0, 3).map((s) => (
              <Cell
                key={s.title}
                inset={50}
                left={<span className={`step-dot ${s.status === 'current' ? 'now' : ''}`} />}
                title={s.title}
                meta={`День ${s.day} · ${fmtDate(s.date)}${s.status === 'current' ? ' · сейчас' : ''}`}
              />
            ))}
            {e.steps.filter((s) => s.status === 'done').length > 0 && (
              <Cell
                inset={50}
                left={<span className="step-dot done"><Check /></span>}
                title={`Пройдено шагов: ${e.steps.filter((s) => s.status === 'done').length}`}
              />
            )}
          </Section>

          <Section header="Опросы">
            {e.surveys.length === 0 && <Cell title="Опросов пока не было" meta="Первый — на 3-й день" />}
            {e.surveys.map((s) => (
              <Cell
                key={s.id}
                title={s.title}
                meta={`День ${s.day}`}
                value={
                  s.status === 'done' ? (
                    <span className="num" style={{ color: 'var(--text)' }}>{s.score}/10</span>
                  ) : (
                    <Chip tone="warning">{s.status === 'missed' ? 'Пропущен' : 'Не пройден'}</Chip>
                  )
                }
              />
            ))}
          </Section>

          <Section header="Обращения">
            {e.tickets.length === 0 && <Cell title="Обращений нет" meta="Все вопросы закрыл бот" />}
            {e.tickets.map((t) => (
              <Cell
                key={t.id}
                onClick={() => nav.push({ name: 'ticket', id: t.id })}
                left={t.red_flag ? <ShieldAlert size={20} color="var(--danger)" /> : <MessageSquare size={20} color="var(--primary)" />}
                inset={50}
                title={<span style={t.red_flag ? { color: 'var(--danger)', fontWeight: 600 } : undefined}>{t.category_label}</span>}
                meta={<span className="clamp-1" style={{ display: 'block' }}>{t.preview}</span>}
                value={<span className="cell-meta">{t.status === 'closed' ? 'закрыто' : fmtAgo(t.created_at)}</span>}
                chevron
              />
            ))}
          </Section>

          {e.intro_text && (
            <Section header="Представление коллегам">
              <div className="intro">{e.intro_text}</div>
            </Section>
          )}

          <Section header="Контакты">
            {[e.manager, e.recruiter].map((c) => (
              <Cell
                key={c.id}
                left={<Avatar name={c.name} />}
                inset={68}
                title={c.name}
                meta={c.role}
                value={
                  c.phone ? (
                    <a className="nav-btn" href={`tel:${c.phone.replace(/[^+\d]/g, '')}`} aria-label={`Позвонить: ${c.name}`}>
                      <Phone size={20} />
                    </a>
                  ) : undefined
                }
              />
            ))}
          </Section>
          <p className="footnote">Данные синтетические, для демонстрации.</p>
        </>
      )}
      {!loading && !e && <Empty icon={<ChevronRight />} title="Не удалось загрузить карточку" />}
    </Screen>
  )
}
