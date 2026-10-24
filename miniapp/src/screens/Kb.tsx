import { useState } from 'react'
import { BookOpen, Check, FileSpreadsheet, FileText, FileUp, MessageSquareText, Pencil, Presentation, Upload } from 'lucide-react'
import { api, toApiError } from '../api/client'
import type { KbDocument } from '../api/types'
import { Cell, Chip, Empty, ErrorState, Screen, Section, Segmented, SkeletonList, fmtNum, plural, useAsync, useToast } from '../components/ui'
import { haptic } from '../lib/bridge'
import { useNav } from '../lib/nav'
import { shortSiteName, useSites } from '../lib/sites'

const KIND_ICON = { pdf: FileText, pptx: Presentation, xlsx: FileSpreadsheet, md: FileText, answer: MessageSquareText }
const KIND_LABEL = { pdf: 'PDF', pptx: 'PPTX', xlsx: 'Сценарий', md: 'Текст', answer: 'Ответ HR' }

const fmtDay = (d: string | null | undefined) => {
  if (!d) return ''
  const t = new Date(d.length === 10 ? `${d}T00:00:00` : d)
  return Number.isNaN(t.getTime()) ? '' : t.toLocaleDateString('ru-RU')
}

function DocRow({ d, onConfirm, busy }: { d: KbDocument; onConfirm: () => void; busy: boolean }) {
  const Icon = KIND_ICON[d.kind] ?? FileText
  const { name } = useSites()
  const sites = d.sites ?? (d.site ? [d.site] : [])
  return (
    <div className="cell" style={{ ['--sep-inset' as string]: '64px', alignItems: 'flex-start' }}>
      <span className={`doc-icon ${d.kind}`}><Icon /></span>
      <div className="cell-body">
        <div className="cell-title doc-title">{d.title}</div>
        <div className="cell-meta num">
          {[KIND_LABEL[d.kind] ?? d.kind.toUpperCase(), fmtDay(d.version_date), `${fmtNum(d.fragments)}\u00A0${plural(d.fragments, ['фрагмент', 'фрагмента', 'фрагментов'])}`]
            .filter(Boolean)
            .join(' · ')}
        </div>
        {(sites.length > 0 || d.status === 'draft') && (
          <div className="chips">
            {d.status === 'draft' && <Chip tone="warning">Черновик</Chip>}
            {sites.map((c) => <Chip key={c}>{shortSiteName(c, name(c))}</Chip>)}
          </div>
        )}
        {d.status === 'draft' && (
          <div className="answer-foot">
            <span className="cell-meta">Бот пока не отвечает по нему</span>
            <button className="btn small secondary fit answer-confirm" onClick={onConfirm} disabled={busy}>
              <Check size={17} /> {busy ? 'Подтверждаем…' : 'Подтвердить'}
            </button>
          </div>
        )}
      </div>
    </div>
  )
}

function AnswerRow({ d, onConfirm, busy }: { d: KbDocument; onConfirm: (text?: string) => void; busy: boolean }) {
  const { name } = useSites()
  const draft = d.status === 'draft'
  const [editing, setEditing] = useState(false)
  const [text, setText] = useState(d.text ?? '')
  const short = (c: string) => shortSiteName(c, name(c))
  const where = d.sites && d.sites.length > 1 ? d.sites.map(short).join(', ') : d.site ? short(d.site) : name(null)
  const meta = [where, fmtDay(d.version_date)].filter(Boolean).join(' · ')
  return (
    <div className="cell answer-cell" style={{ ['--sep-inset' as string]: '16px', alignItems: 'flex-start' }}>
      <div className="cell-body">
        <div className="cell-title doc-title">{d.title}</div>
        {editing ? (
          <textarea
            className="answer-edit"
            value={text}
            onChange={(e) => setText(e.target.value)}
            rows={5}
            aria-label="Текст ответа"
            autoFocus
          />
        ) : (
          d.text && <div className="answer-text">{d.text}</div>
        )}
        <div className="answer-foot">
          <span className="cell-meta num">{meta}</span>
          {draft ? (
            <span className="answer-actions">
              {editing ? (
                <button className="btn small plain fit answer-confirm" onClick={() => { setEditing(false); setText(d.text ?? '') }} disabled={busy}>
                  Отмена
                </button>
              ) : (
                d.text && (
                  <button className="btn small plain fit answer-confirm" onClick={() => { haptic.tap(); setEditing(true) }} disabled={busy}>
                    <Pencil size={16} /> Изменить
                  </button>
                )
              )}
              <button
                className="btn small secondary fit answer-confirm"
                onClick={() => onConfirm(editing && text.trim() !== (d.text ?? '').trim() ? text.trim() : undefined)}
                disabled={busy || (editing && !text.trim())}
              >
                <Check size={17} /> {busy ? 'Подтверждаем…' : 'Подтвердить'}
              </button>
            </span>
          ) : (
            <Chip tone="success" icon={<Check />}>Работает</Chip>
          )}
        </div>
      </div>
    </div>
  )
}

export default function Kb() {
  const nav = useNav()
  const toast = useToast()
  const [tab, setTab] = useState<'docs' | 'answers'>('docs')
  const { data, setData, demo, error, reload, showSkeleton } = useAsync(() => api.documents(), [])
  const [confirming, setConfirming] = useState<number | null>(null)

  const docs = (data ?? []).filter((d) => d.kind !== 'answer')
  const answers = (data ?? []).filter((d) => d.kind === 'answer').sort((a, b) => Number(b.status === 'draft') - Number(a.status === 'draft'))
  const drafts = answers.filter((d) => d.status === 'draft').length
  const total = docs.reduce((s, d) => s + d.fragments, 0)

  const confirm = async (d: KbDocument, text?: string) => {
    haptic.tap()
    setConfirming(d.id)
    try {
      const isAnswer = d.kind === 'answer'
      const next = isAnswer ? await api.confirmAnswer(d.id, text, { demo }) : await api.confirmDocument(d.id, { demo })
      setData((data ?? []).map((x) => (x.id === d.id && x.kind === d.kind ? { ...x, ...next, kind: d.kind } : x)))
      toast('success', isAnswer ? 'Подтверждено. Бот уже отвечает этим текстом' : 'Подтверждено. Бот уже отвечает по документу')
    } catch (e) {
      toast('error', toApiError(e).message)
    } finally {
      setConfirming(null)
    }
  }

  const upload = () => { haptic.tap(); nav.push({ name: 'upload-doc' }) }

  return (
    <Screen
      title="База"
      large
      demo={demo}
      right={
        <button className="nav-btn" aria-label="Загрузить документ" onClick={upload}>
          <Upload size={22} />
        </button>
      }
    >
      <Segmented
        ariaLabel="Раздел базы"
        value={tab}
        onChange={setTab}
        options={[
          { value: 'docs', label: 'Документы' },
          { value: 'answers', label: drafts ? `Ответы HR · ${drafts}` : 'Ответы HR' },
        ]}
      />
      {showSkeleton && <SkeletonList rows={4} />}
      {error && <ErrorState error={error} onRetry={reload} />}

      {data && tab === 'docs' && (
        <>
          {docs.length === 0 ? (
            <Empty
              icon={<BookOpen />}
              title="Документов пока нет"
              text="Загрузите правила, политики и памятки. Бот будет отвечать по ним дословно, со ссылкой на пункт."
            />
          ) : (
            <section className="section">
              <div className="section-header">
                <h2>По ним отвечает бот</h2>
                <span className="num">{fmtNum(total)} {plural(total, ['фрагмент', 'фрагмента', 'фрагментов'])}</span>
              </div>
              <div className="group">
                {docs.map((d) => (
                  <DocRow key={`${d.kind}-${d.id}`} d={d} busy={confirming === d.id} onConfirm={() => confirm(d)} />
                ))}
              </div>
              <div className="section-footer">
                Бот цитирует пункты этих документов и указывает источник. Документы с меткой «КТ» не уходят в облачную нейросеть.
              </div>
            </section>
          )}

          <Section header="Загрузить">
            <Cell
              left={<span className="cell-icon" style={{ background: 'var(--primary)' }}><FileUp /></span>}
              title="Документ"
              meta="PDF или PPTX. Бот начнёт отвечать по нему через минуту"
              onClick={() => nav.push({ name: 'upload-doc' })}
              chevron
            />
            <Cell
              left={<span className="cell-icon" style={{ background: 'var(--success)' }}><FileSpreadsheet /></span>}
              title="Сценарий адаптации"
              meta="xlsx с шагами для площадок"
              onClick={() => nav.push({ name: 'scenario' })}
              chevron
            />
          </Section>
        </>
      )}

      {data && tab === 'answers' && (
        answers.length === 0 ? (
          <Empty
            icon={<MessageSquareText />}
            title="Ответов пока нет"
            text="Когда вы отвечаете новичку в обращении, включите «Добавить ответ в базу». Ответ появится здесь."
          />
        ) : (
          <section className="section">
            <div className="section-header">
              <h2>{drafts ? `Ждут подтверждения: ${drafts} из ${answers.length}` : 'Все ответы работают'}</h2>
            </div>
            <div className="group">
              {answers.map((d) => (
                <AnswerRow key={`a-${d.id}`} d={d} busy={confirming === d.id} onConfirm={(text) => confirm(d, text)} />
              ))}
            </div>
            <div className="section-footer">
              Бот начинает отвечать текстом HR только после подтверждения. До этого такие вопросы уходят к вам.
            </div>
          </section>
        )
      )}
    </Screen>
  )
}
