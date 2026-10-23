import { useState } from 'react'
import {
  AlertTriangle, Briefcase, Check, Clock, Factory, FileCheck2, Megaphone, MessageSquare, MessagesSquare, Phone, QrCode,
  ShieldAlert, UserX,
} from 'lucide-react'
import { EMPLOYEE_CHAT_STATUS } from '../api/chats'
import { api, toApiError } from '../api/client'
import type { Employee, Step } from '../api/types'
import InviteSheet from '../components/InviteSheet'
import {
  Avatar, Cell, Chip, ErrorState, FilePick, Progress, Screen, Section, SkeletonCard, dayLabel, employeeToday, fmtAgo,
  fmtDate, plural, useAsync, useToast,
} from '../components/ui'
import { haptic, openExternal } from '../lib/bridge'
import { useNav } from '../lib/nav'
import { useSites } from '../lib/sites'
import { EmployeeChips } from './Employees'

export function ProbationHeader({ e, hideSignals }: { e: Employee; hideSignals?: boolean }) {
  const { name } = useSites()
  const left = Math.max(0, e.probation_days - e.day)
  // HR завёл выход на выходной: Корсик считает дни от ближайшего рабочего, его и показываем
  const moved = e.first_day !== e.start_date
  return (
    <>
      <div className="hero">
        <Avatar name={e.full_name} large />
        <h2>{e.full_name}</h2>
        <p className="hero-meta">
          {e.position && <span>{e.position}</span>}
          <span className="hero-work">
            {name(e.site)} ·{' '}
            {e.work_type === 'production' ? <Factory size={15} /> : <Briefcase size={15} />}
            {e.work_type === 'production' ? 'производство' : 'офис'}
          </span>
        </p>
        {!hideSignals && <EmployeeChips e={{ ...e, connected: true }} />}
      </div>
      <div className="probation">
        <div className="probation-row">
          <b className="num">{dayLabel(e.day, e.probation_days)}</b>
          <span className="cell-meta num">
            {e.day < 1
              ? `выход ${fmtDate(e.first_day)}`
              : e.day > e.probation_days
                ? `день ${e.day}, срок ${e.probation_days}`
                : `осталось ${left} ${plural(left, ['день', 'дня', 'дней'])}`}
          </span>
        </div>
        <Progress value={e.day / e.probation_days} thick label="Испытательный срок" />
        <div className="probation-sub">
          Испытательный срок · первый день {fmtDate(e.first_day)}
          {moved && `, перенесён с ${fmtDate(e.start_date)}`}
        </div>
      </div>
    </>
  )
}

const IconBox = ({ tone, children }: { tone: string; children: React.ReactNode }) => (
  <span className="cell-icon" style={{ background: tone }}>{children}</span>
)

/** Подключился ли новичок к боту; если нет, ссылку и QR можно показать ещё раз */
function ConnectionSection({ e }: { e: Employee }) {
  const { name } = useSites()
  const [open, setOpen] = useState(false)
  if (e.connected)
    return (
      <Section header="Корсик в «МАКС»">
        <Cell left={<IconBox tone="var(--success)"><Check /></IconBox>} title="Подключился" meta="Корсик ведёт новичка по сценарию" />
      </Section>
    )
  return (
    <Section
      header="Корсик в «МАКС»"
      footer="Если ссылка из оффера потерялась, отправьте её ещё раз или покажите QR-код с экрана."
    >
      <Cell
        left={<IconBox tone="var(--text-secondary)"><UserX /></IconBox>}
        title="Ещё не подключился"
        meta={e.day >= 1 ? 'Уже вышел на работу, а чат с ботом не открыт' : 'Ссылку из оффера ещё не открывали'}
      />
      <Cell
        left={<IconBox tone="var(--primary)"><QrCode /></IconBox>}
        title={<span className="link-text">Ссылка и QR-код</span>}
        meta={e.invite_link ? undefined : 'Сервер не прислал ссылку'}
        onClick={e.invite_link ? () => setOpen(true) : undefined}
        chevron={!!e.invite_link}
      />
      <InviteSheet
        open={open}
        onClose={() => setOpen(false)}
        link={e.invite_link}
        name={e.full_name}
        site={name(e.site)}
        title="Ссылка для новичка"
        text="Отправьте ссылку новичку или дайте отсканировать QR-код. Корсик откроется в «МАКС»."
      />
    </Section>
  )
}

/** Отмашка «представить коллегам» и то, что прислал новичок */
export function IntroSection({ e, demo, canGate, onUpdate }: { e: Employee; demo: boolean; canGate: boolean; onUpdate: (e: Employee) => void }) {
  const toast = useToast()
  const [busy, setBusy] = useState(false)
  const [photoFailed, setPhotoFailed] = useState(false) // ссылка на фото протухла: просто не показываем

  const go = async () => {
    setBusy(true)
    try {
      onUpdate(await api.openGate(e.id, 'recruiter_go', { demo }))
      toast('success', 'Корсик попросит новичка рассказать о себе')
    } catch (err) {
      toast('error', toApiError(err).message)
    } finally {
      setBusy(false)
    }
  }

  if (e.intro)
    return (
      <Section header="Представление коллегам" footer={e.intro.at ? `Прислано ${fmtDate(e.intro.at)}` : undefined}>
        {e.intro.photo_url && !photoFailed && (
          <button className="intro-photo" onClick={() => { haptic.tap(); openExternal(new URL(e.intro!.photo_url!, location.href).href) }} aria-label="Открыть фото целиком">
            <img src={e.intro.photo_url} alt={`Фото: ${e.full_name}`} loading="lazy" onError={() => setPhotoFailed(true)} />
          </button>
        )}
        <div className="intro">{e.intro.text}</div>
      </Section>
    )
  if (e.gates.recruiter_go)
    return (
      <Section header="Представление коллегам">
        <Cell
          left={<IconBox tone="var(--warning)"><Clock /></IconBox>}
          title="Ждём рассказ о себе"
          meta="Корсик попросил новичка коротко рассказать о себе и прислать фото. Ответ появится здесь."
        />
      </Section>
    )
  if (!canGate) return null
  return (
    <Section
      header="Представление коллегам"
      footer="Корсик попросит новичка рассказать о себе и прислать фото. Текст появится здесь, и вы сможете представить человека команде."
    >
      <Cell left={<IconBox tone="var(--primary)"><Megaphone /></IconBox>} title="Знакомство с командой" meta="Обычно на второй рабочий день" />
      <div className="group-action">
        <button className="btn small secondary" onClick={go} disabled={busy}>
          {busy ? 'Отправляем…' : 'Представить коллегам'}
        </button>
      </div>
    </Section>
  )
}

/** Форматы файла плана — как PLAN_EXT в app/api/employees.py: другие сервер не примет */
const PLAN_EXT = ['.pdf', '.doc', '.docx', '.xlsx', '.pptx', '.png', '.jpg', '.jpeg']
const PLAN_FORMATS = 'PDF, Word, Excel, PowerPoint, JPG или PNG'

/** Когда новичок получит план: файл уходит вместе с шагом сценария, а не в момент «План готов» */
function planWhen(e: Employee): string {
  if (e.plan_step.sent) return 'Корсик отправил файл новичку в чат'
  if (e.synthetic) return 'Новичок синтетический, в чат ему ничего не уходит'
  if (!e.connected) return 'Новичок ещё не подключился к боту. Корсик отправит план, когда он подключится'
  const at = e.plan_step.at
  if (at && at.slice(0, 10) > employeeToday(e)) return `Корсик отправит файл вместе с шагом «Адаптационный план» ${fmtDate(at)}`
  return 'Корсик отправит файл новичку в ближайшие минуты'
}

/** «План готов»: HR или руководитель прикладывает файл, бот отправляет его новичку вместе с шагом о плане */
export function PlanSection({ e, demo, onUpdate }: { e: Employee; demo: boolean; onUpdate: (e: Employee) => void }) {
  const toast = useToast()
  const [file, setFile] = useState<File | null>(null)
  const [progress, setProgress] = useState<number | null>(null)
  const fileError =
    file && !PLAN_EXT.some((x) => file.name.toLowerCase().endsWith(x)) ? `Этот формат не подойдёт. Нужен ${PLAN_FORMATS}` : null

  const send = async () => {
    if (!file || fileError) return
    setProgress(0)
    try {
      const next = await api.uploadPlan(e.id, file, { demo, onProgress: setProgress })
      onUpdate(next)
      setFile(null)
      toast('success', `План сохранён. ${planWhen(next)}`)
    } catch (err) {
      toast('error', toApiError(err).message)
    } finally {
      setProgress(null)
    }
  }

  if (e.gates.plan_ready)
    return (
      <Section header="Адаптационный план">
        <Cell
          left={
            <IconBox tone={e.plan_step.sent ? 'var(--success)' : 'var(--primary)'}>
              {e.plan_step.sent ? <FileCheck2 /> : <Clock />}
            </IconBox>
          }
          title="План готов"
          meta={planWhen(e)}
        />
      </Section>
    )
  const sending = progress !== null
  return (
    <Section header="Адаптационный план" footer="Корсик отправит файл новичку вместе с сообщением о плане.">
      <FilePick
        accept={PLAN_EXT.join(',')}
        file={file}
        onPick={setFile}
        label="Файл плана"
        hint={PLAN_FORMATS}
        disabled={sending}
      />
      {fileError && (
        <p className="inline-error" role="alert" style={{ marginBottom: 12 }}>
          <AlertTriangle size={15} />
          {fileError}
        </p>
      )}
      {file && !fileError && (
        <div className="group-action">
          {sending && <Progress value={progress} thick label="Отправка файла" />}
          <button className="btn small" onClick={send} disabled={sending}>
            {sending ? (progress < 1 ? `Отправляем… ${Math.round(progress * 100)}%` : 'Почти готово…') : 'План готов'}
          </button>
        </div>
      )}
    </Section>
  )
}

function stepMeta(s: Step) {
  return [s.day != null ? `День ${s.day}` : null, s.date ? fmtDate(s.date) : null, s.status === 'current' ? 'сейчас' : null]
    .filter(Boolean)
    .join(' · ')
}

export default function EmployeeCard({ id }: { id: number }) {
  const nav = useNav()
  const { data: e, setData, demo, error, reload, showSkeleton } = useAsync(() => api.employee(id), [id])

  const upcoming = e ? e.steps.filter((s) => s.status !== 'done').slice(0, 3) : []
  const doneSteps = e ? e.steps.filter((s) => s.status === 'done').length : 0
  const contacts = e ? [e.manager, e.recruiter].filter((c): c is NonNullable<typeof c> => !!c) : []

  return (
    <Screen title={e?.full_name ?? 'Новичок'} back={{ label: nav.backLabel, onClick: nav.pop }} demo={demo} revealTitleAt={150}>
      {showSkeleton && <SkeletonCard />}
      {error && <ErrorState error={error} onRetry={reload} title={error.status === 404 ? 'Новичок не найден' : undefined} />}
      {e && (
        <>
          <ProbationHeader e={e} />

          <ConnectionSection e={e} />
          <IntroSection e={e} demo={demo} canGate onUpdate={setData} />
          <ChatsSection e={e} />
          <PlanSection e={e} demo={demo} onUpdate={setData} />

          <Section header="Ближайшие шаги">
            {upcoming.length === 0 && doneSteps === 0 && <Cell title="Ближайших шагов нет" />}
            {upcoming.map((s, i) => (
              <Cell
                key={`${s.title}-${i}`}
                inset={50}
                left={<span className={`step-dot ${s.status === 'current' ? 'now' : ''}`} />}
                title={s.title}
                meta={stepMeta(s) || undefined}
              />
            ))}
            {doneSteps > 0 && (
              <Cell inset={50} left={<span className="step-dot done"><Check /></span>} title={`Пройдено шагов: ${doneSteps}`} />
            )}
          </Section>

          <Section header="Опросы">
            {e.surveys.length === 0 && <Cell title="Опросов пока не было" meta="Первый придёт на 3-й день" />}
            {e.surveys.map((s) => (
              <Cell
                key={s.id}
                title={s.title}
                meta={`День ${s.day}`}
                value={
                  s.status === 'done' ? (
                    <span className="num" style={{ color: 'var(--text)' }}>{s.score != null ? `${s.score}/10` : 'пройден'}</span>
                  ) : (
                    <Chip tone="warning">{s.status === 'missed' ? 'Пропущен' : 'Не пройден'}</Chip>
                  )
                }
              />
            ))}
          </Section>

          <Section header="Обращения">
            {e.tickets.length === 0 && <Cell title="Обращений нет" meta="На все вопросы ответил бот" />}
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

          <Section header="Контакты">
            {contacts.length === 0 && <Cell title="Руководитель и рекрутер не указаны" />}
            {contacts.map((c) => (
              <Cell
                key={`${c.role}-${c.id}`}
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
          {e.synthetic && <p className="footnote">Данные синтетические, для демонстрации.</p>}
        </>
      )}
    </Screen>
  )
}

/** Чаты команды: куда Корсик добавил новичка и куда отправил ссылку */
function ChatsSection({ e }: { e: Employee }) {
  const chats = e.chats ?? []
  if (!chats.length) return null
  return (
    <Section header="Чаты команды" footer="Корсик добавляет новичка в чаты в первый рабочий день. Если не пустили настройки приватности, присылает ссылку.">
      {chats.map((c) => (
        <Cell
          key={c.chat_id}
          left={<MessagesSquare size={20} color={c.status === 'added' ? 'var(--success)' : c.status === 'failed' ? 'var(--danger)' : 'var(--primary)'} />}
          inset={50}
          title={<span className="clamp-1">{c.title}</span>}
          meta={[EMPLOYEE_CHAT_STATUS[c.status], c.reason, c.intro && c.status !== 'intro' ? 'опубликовали представление' : null].filter(Boolean).join(' · ')}
          value={c.at ? <span className="cell-meta">{fmtDate(c.at)}</span> : undefined}
        />
      ))}
    </Section>
  )
}

