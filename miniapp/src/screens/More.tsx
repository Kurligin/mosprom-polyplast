import { useState } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { FileBarChart, UserCog } from 'lucide-react'
import { api } from '../api/client'
import type { ShiftTo } from '../api/types'
import { Cell, Screen, Section, Segmented, SkeletonList, useAsync, useToast } from '../components/ui'

const SHIFTS: { value: ShiftTo; label: string }[] = [
  { value: '-2', label: '−2' },
  { value: '1', label: '1' },
  { value: '3', label: '3' },
  { value: '14', label: '14' },
  { value: '60', label: '60' },
  { value: 'end', label: 'конец' },
]

export default function More() {
  const toast = useToast()
  const reduce = useReducedMotion()
  const { data: topics, loading } = useAsync(() => api.topics(), [])
  const [shift, setShift] = useState<ShiftTo>('1')
  const [busy, setBusy] = useState(false)
  const max = Math.max(1, ...(topics ?? []).map((t) => t.count))

  const doShift = async (v: ShiftTo) => {
    setShift(v)
    try {
      const r = await api.shift(v)
      toast('success', r.message)
    } catch {
      toast('error', 'Не удалось перемотать время')
    }
  }

  const report = async () => {
    setBusy(true)
    try {
      const r = await api.report()
      toast(r.ok ? 'success' : 'error', r.message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Screen title="Ещё" large>
      <section className="section">
        <div className="section-header">
          <span>Частые темы вопросов</span>
          <span>за 4 недели</span>
        </div>
        <div className="group">
          {loading && !topics && <SkeletonList rows={3} />}
          {topics && (
            <div className="bars">
              {topics.map((t, i) => (
                <div className="bar-row" key={t.topic}>
                  <span>
                    {t.topic} <span className="bar-days">· {t.peak_days}</span>
                  </span>
                  <span className="bar-count">{t.count}</span>
                  <div className="bar-track">
                    <motion.div
                      className="bar-fill"
                      initial={reduce ? false : { scaleX: 0 }}
                      animate={{ scaleX: t.count / max }}
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
        <div className="section-header">Демо: перемотка времени</div>
        <div className="group" style={{ padding: '12px 16px' }}>
          <div className="cell-meta" style={{ marginBottom: 8 }}>День адаптации тестового новичка</div>
          <Segmented ariaLabel="Перемотка времени" value={shift} onChange={doShift} options={SHIFTS} tall />
        </div>
        <div className="section-footer">Бот сразу присылает сообщения выбранного дня: опросы, напоминания, встречи с руководителем.</div>
      </section>

      <div style={{ marginTop: 16 }}>
        <button className="btn" onClick={report} disabled={busy}>
          <FileBarChart size={20} /> {busy ? 'Формируем…' : 'Сформировать сводку'}
        </button>
      </div>

      <Section header="Режим">
        <Cell
          left={<span className="cell-icon" style={{ background: 'var(--text-secondary)' }}><UserCog /></span>}
          title="Открыть как руководитель"
          chevron
          onClick={() => { location.search = '?role=manager' }}
        />
      </Section>
      <p className="footnote">Корсик 2.0 · демо для хакатона «Открой Моспром».<br />Все данные синтетические.</p>
    </Screen>
  )
}
