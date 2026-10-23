import { useState } from 'react'
import { AlertTriangle, Check, FileSpreadsheet, MapPinPlus } from 'lucide-react'
import { api, isDemoData, toApiError } from '../api/client'
import type { ScenarioReport, SiteCode } from '../api/types'
import { Cell, Chip, FilePick, Progress, Screen, Section, StatNum, fmtNum, plural, useToast } from '../components/ui'
import { haptic } from '../lib/bridge'
import { useNav } from '../lib/nav'
import { refreshSites, useSites } from '../lib/sites'

const TIMEZONES: [string, string][] = [
  ['Europe/Kaliningrad', 'Калининград, UTC+2'],
  ['Europe/Moscow', 'Москва, UTC+3'],
  ['Europe/Samara', 'Самара, UTC+4'],
  ['Asia/Yekaterinburg', 'Екатеринбург, UTC+5'],
  ['Asia/Omsk', 'Омск, UTC+6'],
  ['Asia/Novosibirsk', 'Новосибирск, UTC+7'],
  ['Asia/Krasnoyarsk', 'Красноярск, UTC+7'],
  ['Asia/Irkutsk', 'Иркутск, UTC+8'],
  ['Asia/Yakutsk', 'Якутск, UTC+9'],
  ['Asia/Vladivostok', 'Владивосток, UTC+10'],
  ['Asia/Magadan', 'Магадан, UTC+11'],
  ['Asia/Kamchatka', 'Камчатка, UTC+12'],
]

const steps = (n: number) => `${fmtNum(n)} ${plural(n, ['шаг', 'шага', 'шагов'])}`

/** Новая площадка из xlsx: HR подтверждает название и часовой пояс */
function NewSiteForm({ code, demo }: { code: SiteCode; demo: boolean }) {
  const toast = useToast()
  const [name, setName] = useState(code)
  const [tz, setTz] = useState('Europe/Moscow')
  const [busy, setBusy] = useState(false)
  const [done, setDone] = useState(false)

  const save = async () => {
    if (!name.trim()) return
    setBusy(true)
    try {
      await api.createSite({ code, name: name.trim(), tz }, { demo })
      setDone(true)
      refreshSites()
      toast('success', `Площадка «${name.trim()}» добавлена`)
    } catch (e) {
      toast('error', toApiError(e).message)
    } finally {
      setBusy(false)
    }
  }

  if (done)
    return (
      <Section header={`Площадка ${code}`}>
        <Cell
          left={<span className="cell-icon" style={{ background: 'var(--success)' }}><Check /></span>}
          title={name}
          meta={TIMEZONES.find(([v]) => v === tz)?.[1] ?? tz}
          value={<span className="cell-meta">добавлена</span>}
        />
      </Section>
    )
  return (
    <Section header={`Новая площадка в файле: ${code}`} footer="По часовому поясу бот выбирает, когда присылать сообщения.">
      <label className="field">
        <span className="field-label">Название</span>
        <input value={name} onChange={(e) => setName(e.target.value)} placeholder="Например, Казань" enterKeyHint="done" disabled={busy} />
      </label>
      <label className="field">
        <span className="field-label">Часовой пояс</span>
        <select value={tz} onChange={(e) => setTz(e.target.value)} disabled={busy}>
          {TIMEZONES.map(([v, label]) => <option key={v} value={v}>{label}</option>)}
        </select>
      </label>
      <div className="group-action">
        <button className="btn small" onClick={save} disabled={busy || !name.trim()}>
          <MapPinPlus size={18} /> {busy ? 'Добавляем…' : 'Добавить площадку'}
        </button>
      </div>
    </Section>
  )
}

type Phase = { kind: 'form' } | { kind: 'sending'; progress: number } | { kind: 'report'; report: ScenarioReport; demo: boolean }

export default function ScenarioImport() {
  const nav = useNav()
  const { name: siteName, demo: sitesDemo } = useSites()
  const [file, setFile] = useState<File | null>(null)
  const [phase, setPhase] = useState<Phase>({ kind: 'form' })
  const [error, setError] = useState<string | null>(null)

  // сервер принимает только .xlsx: старый .xls нужно пересохранить в Excel
  const fileError = file && !/\.xlsx$/i.test(file.name) ? 'Нужен файл Excel (.xlsx). Старый .xls пересохраните в Excel как .xlsx' : null

  const send = async () => {
    if (!file || fileError) return
    setError(null)
    setPhase({ kind: 'sending', progress: 0 })
    try {
      const report = await api.importScenario(file, { demo: sitesDemo, onProgress: (p) => setPhase({ kind: 'sending', progress: p }) })
      haptic[report.warnings.length ? 'warning' : 'success']()
      setPhase({ kind: 'report', report, demo: isDemoData(report) })
      refreshSites()
    } catch (e) {
      haptic.error()
      setError(toApiError(e).message)
      setPhase({ kind: 'form' })
    }
  }

  if (phase.kind === 'report') {
    const r = phase.report
    const bySite = Object.entries(r.by_site)
    return (
      <Screen title="Сценарий" back={{ label: nav.backLabel, onClick: nav.pop }} noTabbar demo={phase.demo} revealTitleAt={44}>
        <h1 className="large-title" style={{ padding: '4px 0 0' }}>Сценарий загружен</h1>
        <p className="lead">{file?.name}</p>

        <div className="stats">
          <div className="stat"><StatNum value={r.steps} /><span>{plural(r.steps, ['шаг', 'шага', 'шагов'])}</span></div>
          <div className="stat"><StatNum value={r.surveys} /><span>{plural(r.surveys, ['опрос', 'опроса', 'опросов'])}</span></div>
          <div className="stat"><StatNum value={r.attachments} /><span>{plural(r.attachments, ['вложение', 'вложения', 'вложений'])}</span></div>
        </div>

        {bySite.length > 0 && (
          <Section header="По площадкам">
            {bySite.map(([code, n]) => (
              <Cell
                key={code}
                title={siteName(code)}
                value={<span className="num">{steps(n)}</span>}
              >
                {/* метка под названием: справа место только под число шагов */}
                {r.new_sites.includes(code) && <div className="chips"><Chip tone="primary">новая</Chip></div>}
              </Cell>
            ))}
          </Section>
        )}

        {r.new_sites.map((code) => (
          <NewSiteForm key={code} code={code} demo={phase.demo} />
        ))}

        <Section header={r.warnings.length ? `Проверьте: ${r.warnings.length}` : 'Проверка'}>
          {r.warnings.length === 0 && (
            <Cell left={<span className="cell-icon" style={{ background: 'var(--success)' }}><Check /></span>} title="Замечаний нет" />
          )}
          {r.warnings.map((w, i) => (
            <div className="cell warning-cell" key={i}>
              <AlertTriangle className="warning-icon" />
              <div className="cell-body"><div className="warning-text">{w}</div></div>
            </div>
          ))}
        </Section>

        <div className="stack-actions">
          <button className="btn" onClick={() => { haptic.tap(); nav.pop() }}>
            Готово
          </button>
          <button className="btn plain" onClick={() => { setFile(null); setPhase({ kind: 'form' }) }}>Загрузить другой файл</button>
        </div>
      </Screen>
    )
  }

  const sending = phase.kind === 'sending'
  return (
    <Screen title="Сценарий" back={{ label: nav.backLabel, onClick: nav.pop }} noTabbar demo={sitesDemo} revealTitleAt={44}>
      <h1 className="large-title" style={{ padding: '4px 0 0' }}>Загрузить сценарий</h1>
      <p className="lead">Таблица с шагами адаптации: что и когда бот пишет новичкам на каждой площадке.</p>

      <Section header="Файл" footer="Повторная загрузка обновит шаги, а не задвоит их. Новые площадки из файла попросим подтвердить.">
        <FilePick
          accept=".xlsx,application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
          file={file}
          onPick={(f) => { setFile(f); setError(null) }}
          label="Выбрать файл"
          hint="Excel, как «Финал. ТЗ. Чат-бот Корсик»"
          disabled={sending}
        />
      </Section>
      {fileError && <p className="inline-error"><AlertTriangle size={15} />{fileError}</p>}

      {error && (
        <div className="error-banner" role="alert">
          <AlertTriangle />
          <span>{error}</span>
        </div>
      )}

      <div className="stack-actions">
        {sending ? (
          <div className="upload-status" aria-live="polite">
            <div className="upload-status-row">
              <FileSpreadsheet size={18} />
              <span className="clamp-1">{phase.progress < 1 ? `Загружаем… ${Math.round(phase.progress * 100)}%` : 'Читаем шаги…'}</span>
            </div>
            {phase.progress < 1 ? <Progress value={phase.progress} thick label="Загрузка файла" /> : <div className="progress thick indeterminate"><span /></div>}
          </div>
        ) : (
          <button className="btn" onClick={send} disabled={!file || !!fileError}>
            {error ? 'Попробовать ещё раз' : 'Загрузить сценарий'}
          </button>
        )}
      </div>
    </Screen>
  )
}
