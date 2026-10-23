import { useState } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { BarChart3, FileBarChart, FlaskConical, MessagesSquare, RotateCcw, UserCog } from 'lucide-react'
import { api, demoEnabled, toApiError } from '../api/client'
import type { ShiftTo } from '../api/types'
import { useNav } from '../lib/nav'
import { Cell, Empty, ErrorState, Screen, Section, Segmented, useAsync, useToast } from '../components/ui'

const SHIFTS: { value: ShiftTo; label: string }[] = [
  { value: '-2', label: '−2' },
  { value: '1', label: '1' },
  { value: '3', label: '3' },
  { value: '14', label: '14' },
  { value: '60', label: '60' },
  { value: 'end', label: 'конец' },
]

function TopicsSkeleton() {
  return (
    <div className="bars" aria-hidden>
      {[70, 55, 40, 30].map((w) => (
        <div className="bar-row" key={w}>
          <div className="skeleton" style={{ width: `${w}%`, height: 14 }} />
          <span />
          <div className="bar-track"><div className="skeleton" style={{ width: `${w}%`, height: '100%' }} /></div>
        </div>
      ))}
    </div>
  )
}

export default function More() {
  const toast = useToast()
  const nav = useNav()
  const reduce = useReducedMotion()
  const topics = useAsync(() => api.topics(), [])
  // перематывать можно только тестовых новичков команды: у живого человека перемотка пропустит шаги
  // и сразу пришлёт ему опросы (сервер ответит 403), синтетике бот не пишет вовсе
  const people = useAsync(() => api.employees(), [])
  const testers = (people.data ?? []).filter((e) => e.test)
  // DEMO_ENABLED=false: демо-эндпоинты отвечают 404, блок демо прячем
  const [demoOff, setDemoOff] = useState(demoEnabled === false)
  const [who, setWho] = useState<number | ''>('')
  const [shift, setShift] = useState<ShiftTo | null>(null)
  const [shifting, setShifting] = useState(false)
  const [busy, setBusy] = useState(false)
  const list = topics.data ?? []
  const max = Math.max(1, ...list.map((t) => t.count))

  const fail = (e: unknown) => {
    const err = toApiError(e)
    if (err.status === 404 && /демо/i.test(err.message)) {
      setDemoOff(true)
      toast('info', 'Демо на этом сервере выключено')
      return
    }
    // 403 — живой новичок, 404 — тестового новичка нет: сервер присылает понятный текст
    toast('error', err.message)
  }

  const doShift = async (v: ShiftTo) => {
    setShift(v === 'now' ? null : v)
    setShifting(true)
    try {
      const r = await api.shift(v, who === '' ? undefined : who, { demo: people.demo })
      toast('success', r.message)
    } catch (e) {
      setShift(null)
      fail(e)
    } finally {
      setShifting(false)
    }
  }

  const report = async () => {
    setBusy(true)
    try {
      const r = await api.report({ demo: topics.demo })
      toast(r.ok ? 'success' : 'warning', r.message)
    } catch (e) {
      fail(e)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Screen title="Ещё" large demo={topics.demo || people.demo}>
      <section className="section">
        <div className="section-header">
          <span>О чём спрашивают чаще</span>
          <span>за 4 недели</span>
        </div>
        <div className="group">
          {topics.showSkeleton && <TopicsSkeleton />}
          {topics.error && <ErrorState error={topics.error} onRetry={topics.reload} />}
          {topics.data && list.length === 0 && (
            <Empty icon={<BarChart3 />} title="Вопросов пока мало" text="Темы появятся, когда новички начнут спрашивать бота." />
          )}
          {list.length > 0 && (
            <div className="bars">
              {list.map((t, i) => (
                <div className="bar-row" key={t.topic}>
                  <span className="bar-label">
                    {t.topic} {t.peak_days && <span className="bar-days">· {t.peak_days}</span>}
                  </span>
                  <span className="bar-count">{t.count.toLocaleString('ru-RU')}</span>
                  <div className="bar-track">
                    <motion.div
                      className="bar-fill"
                      initial={reduce ? false : { transform: 'scaleX(0)' }}
                      animate={{ transform: `scaleX(${t.count / max})` }}
                      transition={{ duration: 0.5, delay: i * 0.03, ease: [0.23, 1, 0.32, 1] }}
                    />
                  </div>
                </div>
              ))}
            </div>
          )}
        </div>
        <div className="section-footer">По этим темам видно, что добавить в базу и в сценарий первых дней.</div>
      </section>

      {demoOff ? (
        <Section header="Демо" footer="Машина времени и отправка сводки по кнопке работают, когда на сервере включено демо (DEMO_ENABLED=true).">
          <Cell
            left={<span className="cell-icon" style={{ background: 'var(--text-secondary)' }}><FlaskConical /></span>}
            title="Демо выключено"
            meta="Сводка уходит руководителю HR сама, по понедельникам в 9:00"
          />
        </Section>
      ) : (
        <>
        <section className="section">
          <div className="section-header">Демо: машина времени</div>
          <div className="group">
            <label className="field">
              <span className="field-label">Кого перематываем</span>
              <select value={who} onChange={(e) => setWho(e.target.value ? Number(e.target.value) : '')} disabled={shifting}>
                <option value="">Первый тестовый новичок</option>
                {testers.map((e) => <option key={e.id} value={e.id}>{e.full_name}</option>)}
              </select>
            </label>
            <div className="field">
              <span className="field-label">На какой день адаптации</span>
              <Segmented
                ariaLabel="День адаптации"
                value={shift ?? ('' as ShiftTo)}
                onChange={doShift}
                options={SHIFTS}
                tall
              />
            </div>
          </div>
          <div className="section-footer">
            {shifting ? 'Перематываем…' : 'Бот сразу пришлёт сообщения этого дня: шаги, опросы, напоминания руководителю.'}
          </div>
          {shift && shift !== 'now' && (
            <div className="stack-actions" style={{ marginTop: 8 }}>
              <button className="btn plain small" disabled={shifting} onClick={() => doShift('now')}>
                <RotateCcw size={17} /> Вернуть к сегодняшнему дню
              </button>
            </div>
          )}
        </section>

        <div style={{ marginTop: 16 }}>
          <button className="btn" onClick={report} disabled={busy}>
            <FileBarChart size={20} /> {busy ? 'Собираем…' : 'Отправить сводку'}
          </button>
          <p className="footnote" style={{ paddingTop: 8 }}>
            Уйдёт руководителю HR в «МАКС». Без имён и цитат, площадка попадает в сводку, если ответили хотя бы 5 человек.
          </p>
        </div>
        </>
      )}

      <Section header="Чаты команды" footer="Кому из новичков нужен каждый чат и какая группа получает обращения.">
        <Cell
          left={<span className="cell-icon" style={{ background: 'var(--primary)' }}><MessagesSquare /></span>}
          title="Чаты команды"
          chevron
          onClick={() => nav.push({ name: 'chats' })}
        />
      </Section>

      <Section header="Режим">
        <Cell
          left={<span className="cell-icon" style={{ background: 'var(--text-secondary)' }}><UserCog /></span>}
          title="Посмотреть глазами руководителя"
          chevron
          onClick={() => {
            const q = new URLSearchParams(location.search)
            q.set('role', 'manager')
            location.search = q.toString()
          }}
        />
      </Section>
      <p className="footnote">Корсик 2.0 · хакатон «Открой Моспром».<br />Данные в демо синтетические.</p>
    </Screen>
  )
}
