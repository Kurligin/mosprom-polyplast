import { useState } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { BarChart3, FileBarChart, RotateCcw, UserCog } from 'lucide-react'
import { api, toApiError } from '../api/client'
import type { ShiftTo } from '../api/types'
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
  const reduce = useReducedMotion()
  const topics = useAsync(() => api.topics(), [])
  // перематывать можно только тех, кто подключился к боту: остальным бот не пишет
  const people = useAsync(() => api.employees(), [])
  const connected = (people.data ?? []).filter((e) => e.connected)
  const [who, setWho] = useState<number | ''>('')
  const [shift, setShift] = useState<ShiftTo | null>(null)
  const [shifting, setShifting] = useState(false)
  const [busy, setBusy] = useState(false)
  const list = topics.data ?? []
  const max = Math.max(1, ...list.map((t) => t.count))

  const doShift = async (v: ShiftTo) => {
    setShift(v === 'now' ? null : v)
    setShifting(true)
    try {
      const r = await api.shift(v, who === '' ? undefined : who, { demo: people.demo })
      toast('success', r.message)
    } catch (e) {
      toast('error', toApiError(e).message)
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
      toast('error', toApiError(e).message)
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

      <section className="section">
        <div className="section-header">Демо: машина времени</div>
        <div className="group">
          <label className="field">
            <span className="field-label">Кого перематываем</span>
            <select value={who} onChange={(e) => setWho(e.target.value ? Number(e.target.value) : '')} disabled={shifting}>
              <option value="">Первый тестовый новичок</option>
              {connected.map((e) => <option key={e.id} value={e.id}>{e.full_name}</option>)}
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
          Уйдёт главе подбора в «МАКС». Без имён и цитат, площадка попадает в сводку, если ответили хотя бы 5 человек.
        </p>
      </div>

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
