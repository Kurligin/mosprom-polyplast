import { useState } from 'react'
import { AlertTriangle, Check, CheckCircle2, FileText, Lock } from 'lucide-react'
import { api, toApiError, isDemoData } from '../api/client'
import type { KbDocument, SiteCode } from '../api/types'
import { Cell, FilePick, Progress, Screen, Section, Switch, plural, useAsync } from '../components/ui'
import { haptic } from '../lib/bridge'
import { useNav } from '../lib/nav'
import { useSites } from '../lib/sites'

const MAX_MB = 50
const extOf = (name: string) => name.split('.').pop()?.toLowerCase() ?? ''
const baseName = (name: string) => name.replace(/\.[^.]+$/, '').replace(/[_]+/g, ' ').trim()

type Phase = { kind: 'form' } | { kind: 'sending'; progress: number } | { kind: 'done'; doc: KbDocument; demo: boolean }

export default function UploadDoc() {
  const nav = useNav()
  const { sites, demo: sitesDemo } = useSites()
  // если сама база на демо-данных, загрузка тоже демо: иначе документ «пропадёт» из списка
  const docs = useAsync(() => api.documents(), [])
  const demo = docs.demo || sitesDemo

  const [file, setFile] = useState<File | null>(null)
  const [title, setTitle] = useState('')
  const [titleTouched, setTitleTouched] = useState(false)
  const [kt, setKt] = useState(false)
  const [picked, setPicked] = useState<SiteCode[]>([])
  const [phase, setPhase] = useState<Phase>({ kind: 'form' })
  const [error, setError] = useState<string | null>(null)

  const fileError = file
    ? !['pdf', 'pptx'].includes(extOf(file.name))
      ? 'Подойдёт PDF или PPTX'
      : file.size > MAX_MB * 1024 * 1024
        ? `Файл больше ${MAX_MB} МБ`
        : null
    : null

  const pick = (f: File | null) => {
    setFile(f)
    setError(null)
    if (f && !titleTouched) setTitle(baseName(f.name))
  }

  const toggleSite = (code: SiteCode) => {
    haptic.select()
    setPicked((p) => (p.includes(code) ? p.filter((c) => c !== code) : [...p, code]))
  }

  const send = async () => {
    if (!file || fileError) return
    setError(null)
    setPhase({ kind: 'sending', progress: 0 })
    try {
      const doc = await api.uploadDocument(
        { file, title: title.trim() || baseName(file.name), is_kt: kt, site_codes: picked },
        { demo, onProgress: (p) => setPhase({ kind: 'sending', progress: p }) },
      )
      haptic.success()
      setPhase({ kind: 'done', doc, demo: isDemoData(doc) })
    } catch (e) {
      haptic.error()
      setError(toApiError(e).message)
      setPhase({ kind: 'form' })
    }
  }

  const again = () => {
    setFile(null)
    setTitle('')
    setTitleTouched(false)
    setKt(false)
    setPicked([])
    setPhase({ kind: 'form' })
  }

  if (phase.kind === 'done') {
    const n = phase.doc.chunks ?? phase.doc.fragments
    return (
      <Screen title="Документ" back={{ label: nav.backLabel, onClick: nav.pop }} noTabbar demo={phase.demo} revealTitleAt={44}>
        <div className="result">
          <CheckCircle2 className="result-icon success" />
          <h2>Готово: {n} {plural(n, ['фрагмент', 'фрагмента', 'фрагментов'])}</h2>
          <p>«{phase.doc.title}» в базе. Бот уже отвечает по нему{phase.doc.trade_secret ? ', а в облачную нейросеть документ не уходит' : ''}.</p>
        </div>
        <div className="stack-actions">
          <button className="btn" onClick={() => { haptic.tap(); nav.pop() }}>К базе</button>
          <button className="btn plain" onClick={again}>Загрузить ещё</button>
        </div>
      </Screen>
    )
  }

  const sending = phase.kind === 'sending'
  return (
    <Screen title="Новый документ" back={{ label: nav.backLabel, onClick: nav.pop }} noTabbar demo={demo} revealTitleAt={44}>
      <h1 className="large-title" style={{ padding: '4px 0 0' }}>Загрузить документ</h1>
      <p className="lead">Бот разберёт документ на пункты и начнёт отвечать по нему дословно, со ссылкой на источник.</p>

      <Section header="Файл">
        <FilePick
          accept=".pdf,.pptx,application/pdf,application/vnd.openxmlformats-officedocument.presentationml.presentation"
          file={file}
          onPick={pick}
          label="Выбрать файл"
          hint="PDF или PPTX, до 50 МБ"
          disabled={sending}
        />
        {file && (
          <label className="field">
            <span className="field-label">Название в базе</span>
            <input
              value={title}
              onChange={(e) => { setTitle(e.target.value); setTitleTouched(true) }}
              placeholder="Правила внутреннего трудового распорядка"
              disabled={sending}
              enterKeyHint="done"
            />
          </label>
        )}
      </Section>
      {fileError && <p className="inline-error"><AlertTriangle size={15} />{fileError}</p>}

      <Section footer="Такой документ бот ищет и цитирует сам, без облачной нейросети. Подходит для положения о коммерческой тайне.">
        <Cell
          left={<span className="cell-icon" style={{ background: 'var(--danger)' }}><Lock /></span>}
          title="Коммерческая тайна"
          value={<Switch checked={kt} onChange={setKt} label="Коммерческая тайна" disabled={sending} />}
        />
      </Section>

      <Section header="Для каких площадок" footer="Если ничего не выбрать, документ работает для всех площадок.">
        <Cell
          left={<span className={`check${picked.length === 0 ? ' on' : ''}`}><Check /></span>}
          title="Все площадки"
          inset={52}
          onClick={() => { haptic.select(); setPicked([]) }}
          disabled={sending}
        />
        {sites.map((s) => (
          <Cell
            key={s.code}
            left={<span className={`check${picked.includes(s.code) ? ' on' : ''}`}><Check /></span>}
            title={s.name}
            inset={52}
            onClick={() => toggleSite(s.code)}
            disabled={sending}
          />
        ))}
      </Section>

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
              <FileText size={18} />
              <span className="clamp-1">{phase.progress < 1 ? `Загружаем… ${Math.round(phase.progress * 100)}%` : 'Разбираем на фрагменты…'}</span>
            </div>
            {phase.progress < 1 ? <Progress value={phase.progress} thick label="Загрузка файла" /> : <div className="progress thick indeterminate"><span /></div>}
          </div>
        ) : (
          <button className="btn" onClick={send} disabled={!file || !!fileError}>
            {error ? 'Попробовать ещё раз' : 'Загрузить'}
          </button>
        )}
      </div>
    </Screen>
  )
}
