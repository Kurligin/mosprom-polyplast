import { useMemo, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { ArrowDownRight, ArrowUpRight, ChartColumnIncreasing, ChevronDown, ChevronRight, Minus, Plus, ShieldCheck } from 'lucide-react'
import { api, type ApiError } from '../api/client'
import type { Analytics as AnalyticsData, Cluster, FunnelStage, MoodSite, MoodWeek, RiskItem, SiteCode } from '../api/types'
import { Avatar, Empty, ErrorState, Screen, Segmented, SkeletonList, StatNum, fmtNum, plural, useAsync } from '../components/ui'
import { haptic } from '../lib/bridge'
import { useNav } from '../lib/nav'
import { shortSiteName } from '../lib/sites'

const pct = (x: number | null | undefined) => (x === null || x === undefined ? '' : `${Math.round(x * 100)}%`)
const num1 = (x: number) => x.toFixed(1).replace('.', ',')

const shortSite = shortSiteName

const STAGE_HINT: Partial<Record<FunnelStage['stage'], string>> = { done: 'за 3 месяца' }
const MEDIUM_PREVIEW = 3

export default function Analytics() {
  const [site, setSite] = useState<SiteCode | ''>('')
  const summary = useAsync(() => api.analytics(site || null), [site])
  const risks = useAsync(() => api.risks(site || null), [site])
  const data = summary.data
  const sites = data?.sites ?? []
  const demo = summary.demo || risks.demo
  const siteName = (code: string) => shortSite(code, sites.find((x) => x.code === code)?.name ?? code)
  // сегмент, пока короткие названия помещаются в строку на узком телефоне; иначе обычный список
  const asSegment = sites.length > 1 && sites.length <= 4 && sites.reduce((n, s) => n + shortSite(s.code, s.name).length, 3) <= 26

  return (
    <Screen title="Аналитика" large demo={demo}>
      {asSegment && (
        <div className="an-filter">
          <Segmented
            ariaLabel="Площадка"
            value={site}
            onChange={setSite}
            options={[{ value: '' as SiteCode | '', label: 'Все' }, ...sites.map((s) => ({ value: s.code, label: shortSite(s.code, s.name) }))]}
          />
        </div>
      )}
      {sites.length > 1 && !asSegment && (
        <section className="section">
          <div className="group">
            <label className="field">
              <span className="field-label">Площадка</span>
              <select value={site} onChange={(e) => setSite(e.target.value)}>
                <option value="">Все площадки</option>
                {sites.map((s) => <option key={s.code} value={s.code}>{s.name}</option>)}
              </select>
            </label>
          </div>
        </section>
      )}

      {summary.showSkeleton && <AnalyticsSkeleton />}
      {summary.error && <ErrorState error={summary.error} onRetry={summary.reload} />}
      {data && (
        <>
          <Stats data={data} />
          <RiskSection
            items={risks.data}
            loading={risks.showSkeleton}
            error={risks.error}
            onRetry={risks.reload}
            siteName={siteName}
          />
          <FunnelSection funnel={data.funnel} />
          <MoodSection weeks={data.mood.weeks} sites={data.mood.sites} lowData={data.mood.low_data} threshold={data.threshold} />
          <ClusterSection clusters={data.clusters} lowData={!!data.clusters_low_data} threshold={data.threshold} siteName={siteName} />
          <p className="footnote">
            Зона риска считается по понятным правилам: сигналы, оценки, пропуски опросов, молчание, встречи и обращения без ответа.
            Срезы настроения показываем от {data.threshold} ответивших.{data.synthetic ? ' Данные синтетические.' : ''}
          </p>
        </>
      )}
    </Screen>
  )
}

/** Повторяет сетку экрана: фильтр площадок, три плитки той же высоты, список */
function AnalyticsSkeleton() {
  return (
    <div aria-hidden>
      <div className="an-filter"><div className="skeleton" style={{ height: 36, borderRadius: 9 }} /></div>
      <div className="stats">
        {[0, 1, 2].map((i) => (
          <div className="stat" key={i}>
            <div className="skeleton" style={{ width: 36, height: 24, margin: '2px 0' }} />
            <div className="skeleton" style={{ width: '80%', height: 13, margin: '2px 0 3px' }} />
          </div>
        ))}
      </div>
      <SkeletonList rows={4} />
    </div>
  )
}

function Stats({ data }: { data: AnalyticsData }) {
  const { risks, kpi } = data
  return (
    <div className="stats">
      <div className="stat">
        <StatNum value={kpi.newbies} />
        <span>в адаптации</span>
      </div>
      <div className="stat">
        <StatNum value={risks.high} className={risks.high ? 'tone-danger' : ''} />
        <span>высокий риск</span>
      </div>
      <div className="stat">
        <StatNum value={risks.medium} className={risks.medium ? 'tone-warning' : ''} />
        <span>средний риск</span>
      </div>
    </div>
  )
}

/* ---------- В зоне риска ---------- */

function RiskSection({ items, loading, error, onRetry, siteName }: {
  items: RiskItem[] | null; loading: boolean; error: ApiError | null; onRetry: () => void; siteName: (code: string) => string
}) {
  const nav = useNav()
  const [all, setAll] = useState(false)
  const high = (items ?? []).filter((i) => i.level === 'high')
  const medium = (items ?? []).filter((i) => i.level === 'medium')
  const shownMedium = all ? medium : medium.slice(0, MEDIUM_PREVIEW)
  const hidden = medium.length - shownMedium.length
  const row = (r: RiskItem) => (
    <RiskRow key={r.employee_id} r={r} site={siteName(r.site)} onClick={() => nav.push({ name: 'employee', id: r.employee_id })} />
  )

  if (error) return <section className="section"><div className="group"><ErrorState error={error} onRetry={onRetry} /></div></section>
  // список риска грузится отдельно от сводки: место под него занято сразу, этапы ниже не прыгают
  if (!items) return <SkeletonList rows={3} invisible={!loading} />
  if (items.length === 0) {
    return (
      <section className="section">
        <div className="group">
          <Empty
            icon={<ShieldCheck />}
            title="В зоне риска никого"
            text="Корсик следит за сигналами, оценками и молчанием в опросах. Если что-то изменится, новичок появится здесь."
          />
        </div>
      </section>
    )
  }
  return (
    <>
      {high.length > 0 && (
        <section className="section">
          <div className="section-header">
            <h2 className="risk-head high">Высокий риск</h2>
            <span className="num">{high.length}</span>
          </div>
          <div className="group">{high.map(row)}</div>
          {medium.length === 0 && <div className="section-footer">{FOOT}</div>}
        </section>
      )}
      {medium.length > 0 && (
        <section className="section">
          <div className="section-header">
            <h2 className="risk-head medium">Средний риск</h2>
            <span className="num">{medium.length}</span>
          </div>
          <div className="group">
            {shownMedium.map(row)}
            {hidden > 0 && (
              <button className="cell more-row" onClick={() => { haptic.tap(); setAll(true) }}>
                <span className="cell-title">Показать ещё {hidden}</span>
                <ChevronDown className="chev" />
              </button>
            )}
          </div>
          <div className="section-footer">{FOOT}</div>
        </section>
      )}
    </>
  )
}

const FOOT = 'Нажмите на новичка, чтобы открыть карточку: опросы, обращения, встречи.'

function RiskRow({ r, site, onClick }: { r: RiskItem; site: string; onClick: () => void }) {
  const reasons = r.reasons.slice(0, 3)
  const rest = r.reasons.length - reasons.length
  const label = r.level === 'high' ? 'высокий риск' : 'средний риск'
  return (
    <button
      className="cell risk-cell"
      style={{ ['--sep-inset' as string]: '68px' }}
      onClick={() => { haptic.tap(); onClick() }}
      aria-label={`${r.full_name}, ${label}: ${r.reasons.map((x) => x.text).join('; ')}`}
    >
      <span className={`risk-avatar ${r.level}`}>
        <Avatar name={r.full_name} />
      </span>
      <div className="cell-body">
        <div className="cell-title strong clamp-2">{r.full_name}</div>
        <div className="cell-meta num clamp-1">
          {site} · {r.day < 1 ? 'до выхода' : `день ${r.day}`}
          {r.position ? ` · ${r.position}` : ''}
        </div>
        <ul className="reasons">
          {reasons.map((x, i) => (
            <li key={`${x.code}-${i}`} className={x.level}>{x.text}</li>
          ))}
          {rest > 0 && <li className="more">и ещё {rest}</li>}
        </ul>
      </div>
      <ChevronRight className="chev" />
    </button>
  )
}

/* ---------- Этапы ---------- */

function FunnelSection({ funnel }: { funnel: FunnelStage[] }) {
  const reduce = useReducedMotion()
  const max = Math.max(1, ...funnel.map((f) => f.count))
  if (!funnel.length) return null
  return (
    <section className="section">
      <div className="section-header">
        <h2>Этапы адаптации</h2>
        <span>сейчас</span>
      </div>
      <div className="group">
        <div className="bars">
          {funnel.map((f, i) => (
            <div className="bar-row" key={f.stage}>
              <span className="bar-label">
                {f.label}
                {STAGE_HINT[f.stage] && <span className="bar-days"> · {STAGE_HINT[f.stage]}</span>}
                {f.risk > 0 && (
                  <span className={`stage-risk ${f.risk_high ? 'high' : 'medium'}`}> · в зоне риска {fmtNum(f.risk)}</span>
                )}
              </span>
              <span className="bar-count">{fmtNum(f.count)}</span>
              <div className="bar-track">
                <motion.div
                  className={`bar-fill${f.stage === 'done' ? ' muted' : ''}`}
                  initial={reduce ? false : { transform: 'scaleX(0)' }}
                  animate={{ transform: `scaleX(${f.count / max})` }}
                  transition={{ duration: 0.5, delay: i * 0.03, ease: [0.23, 1, 0.32, 1] }}
                />
              </div>
            </div>
          ))}
        </div>
      </div>
    </section>
  )
}

/* ---------- Настроение ---------- */

function weekLabel(w: MoodWeek) {
  const [, m, d] = (w.start ?? '').split('-')
  return m && d ? `${Number(d)}.${m}` : w.label || ''
}

function MoodSection({ weeks, sites, lowData, threshold }: { weeks: MoodWeek[]; sites: MoodSite[]; lowData: string[]; threshold: number }) {
  const reduce = useReducedMotion()
  const anyShown = weeks.some((w) => w.shown)
  if (!weeks.length && !sites.length) return null
  return (
    <section className="section">
      <div className="section-header">
        <h2>Настроение по неделям</h2>
        <span>доля позитива</span>
      </div>
      <div className="group">
        <div className="mood-chart" role="img" aria-label={weeks.map((w) => `Неделя с ${weekLabel(w)}: ${w.shown ? `позитив ${pct(w.mood_share.positive)}, негатив ${pct(w.mood_share.negative)}` : 'мало ответов'}`).join('. ')}>
          {weeks.map((w, i) => {
            const s = w.mood_share
            const has = w.shown && w.mood_total > 0
            return (
              <div className={`mood-col${w.current ? ' current' : ''}`} key={`${w.start}-${i}`}>
                <span className="mood-pct num">{has ? pct(s.positive) : ''}</span>
                <div className={`mood-stack${has ? '' : ' blank'}`}>
                  {has && (
                    <motion.div
                      className="mood-fill"
                      initial={reduce ? false : { transform: 'scaleY(0)' }}
                      animate={{ transform: 'scaleY(1)' }}
                      transition={{ duration: 0.45, delay: i * 0.03, ease: [0.23, 1, 0.32, 1] }}
                    >
                      <i className="neg" style={{ flexGrow: s.negative ?? 0 }} />
                      <i className="neu" style={{ flexGrow: s.neutral ?? 0 }} />
                      <i className="pos" style={{ flexGrow: s.positive ?? 0 }} />
                    </motion.div>
                  )}
                </div>
                <span className="mood-week num">{weekLabel(w)}</span>
              </div>
            )
          })}
        </div>
        <div className="mood-legend">
          <span><i className="pos" />позитив</span>
          <span><i className="neu" />нейтрально</span>
          <span><i className="neg" />негатив</span>
        </div>
        {sites.map((s, i) => <MoodSiteRow key={`${s.code}-${i}`} s={s} />)}
      </div>
      <div className="section-footer">
        {anyShown ? '' : 'Пока мало ответов. '}
        Пустой столбец: ответили меньше {threshold} человек, цифр не показываем.
        {lowData.length > 0 && ` Мало данных: ${lowData.join(', ')}.`}
      </div>
    </section>
  )
}

function MoodSiteRow({ s }: { s: MoodSite }) {
  const share = s.mood_share
  return (
    <div className="cell mood-site">
      <div className="cell-body">
        <div className="mood-site-top">
          <span className="cell-title strong clamp-1" title={s.name}>{s.name.length > 24 ? shortSite(s.code, s.name) : s.name}</span>
          <span className="mood-site-pct num">{pct(share.positive)}</span>
        </div>
        <div className="cell-meta num">
          {fmtNum(s.respondents)} из {fmtNum(s.newbies)} {plural(s.respondents, ['ответил', 'ответили', 'ответили'])}
          {s.avg_score !== null ? ` · оценка ${num1(s.avg_score)}` : ''}
          {s.low_data ? ' · мало данных' : ''}
        </div>
        <div className="mood-bar" aria-hidden>
          {s.mood_total > 0 ? (
            <>
              <i className="pos" style={{ flexGrow: s.mood.positive }} />
              <i className="neu" style={{ flexGrow: s.mood.neutral }} />
              <i className="neg" style={{ flexGrow: s.mood.negative }} />
            </>
          ) : (
            <i className="none" style={{ flexGrow: 1 }} />
          )}
        </div>
      </div>
    </div>
  )
}

/* ---------- Темы ---------- */

function ClusterSection({ clusters, lowData, threshold, siteName }: {
  clusters: Cluster[]; lowData: boolean; threshold: number; siteName: (code: string) => string
}) {
  const [open, setOpen] = useState<string | null>(null)
  const list = useMemo(() => clusters.filter((c) => c.count > 0), [clusters])
  return (
    <section className="section">
      <div className="section-header">
        <h2>О чём пишут в опросах</h2>
        <span>за 4 недели</span>
      </div>
      <div className="group">
        {list.length === 0 && (lowData ? (
          <Empty icon={<ShieldCheck />} title="Мало ответов"
            text={`Темы покажем, когда за 4 недели ответят хотя бы ${threshold} новичков: так по теме не узнать автора.`} />
        ) : (
          <Empty icon={<ChartColumnIncreasing />} title="Тем пока мало" text="Темы появятся, когда новички напишут о трудностях в опросах." />
        ))}
        {list.map((c, i) => (
          <ClusterRow
            key={`${c.topic}-${i}`}
            c={c}
            siteName={siteName}
            open={open === c.topic}
            onToggle={() => setOpen(open === c.topic ? null : c.topic)}
            threshold={threshold}
          />
        ))}
      </div>
      {list.length > 0 && (
        <div className="section-footer">
          Темы и настроение определяет ИИ по ответам. Тему одного человека не показываем. Цитаты без имён,
          когда о теме пишут от {threshold} новичков, и только от тех, кто согласился на ИИ-разбор.
        </div>
      )}
    </section>
  )
}

const TREND_ICON = { up: ArrowUpRight, new: Plus, down: ArrowDownRight, flat: Minus }

const NO_QUOTES: Record<string, (threshold: number) => string> = {
  few: (n) => `Цитаты показываем, когда о теме пишут от ${n} новичков.`,
  no_text: () => 'Цитат нет: ответили кнопками, без своего текста.',
}

function ClusterRow({ c, open, onToggle, siteName, threshold }: {
  c: Cluster; open: boolean; onToggle: () => void; siteName: (code: string) => string; threshold: number
}) {
  const reduce = useReducedMotion()
  const Icon = TREND_ICON[c.trend] ?? Minus
  const tone = c.trend === 'up' || c.trend === 'new' ? 'danger' : c.trend === 'down' ? 'success' : ''
  const where = c.sites.filter((s) => s.count > 0)
    .map((s) => `${s.code === 'other' ? (s.name ?? 'Другие площадки') : siteName(s.code)} ${fmtNum(s.count)}`).join(' · ')
  const quotes = open ? c.quotes : c.quotes.slice(0, 1)
  return (
    <button className="cell cluster-cell" aria-expanded={open} onClick={() => { haptic.select(); onToggle() }}>
      <div className="cell-body">
        <div className="cluster-top">
          <span className="cell-title strong">{c.label}</span>
          <span className="cluster-count num">
            {fmtNum(c.count)} <small>{plural(c.count, ['упоминание', 'упоминания', 'упоминаний'])}</small>
          </span>
        </div>
        <div className="chips">
          {c.trend_text && <span className={`chip wrap ${tone}`}><Icon /><span className="chip-text">{c.trend_text}</span></span>}
        </div>
        {quotes.map((q, i) => (
          <blockquote key={i} className={`quote${open ? '' : ' clamp-2'}`}>{q.text}</blockquote>
        ))}
        <AnimatePresence initial={false}>
          {open && (
            <motion.div
              className="cluster-more"
              initial={reduce ? { opacity: 0 } : { opacity: 0, transform: 'translateY(-4px)' }}
              animate={{ opacity: 1, transform: 'translateY(0px)' }}
              exit={{ opacity: 0 }}
              transition={{ duration: 0.18, ease: [0.23, 1, 0.32, 1] }}
            >
              <div className="cell-meta num">
                {fmtNum(c.people)} {plural(c.people, ['новичок', 'новичка', 'новичков'])}{where ? ` · ${where}` : ''}
                {c.prev > 0 ? ` · месяц назад ${fmtNum(c.prev)}` : ''}
              </div>
              {c.quotes.length === 0 && (
                <div className="cell-meta">{NO_QUOTES[c.quotes_reason ?? '']?.(threshold) ?? 'Цитат нет.'}</div>
              )}
            </motion.div>
          )}
        </AnimatePresence>
      </div>
      <ChevronDown className={`chev cluster-chev${open ? ' open' : ''}`} />
    </button>
  )
}
