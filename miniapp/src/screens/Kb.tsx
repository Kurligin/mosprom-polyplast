import { useState } from 'react'
import { FileSpreadsheet, FileText, Lock, MessageSquareText, Presentation, Upload } from 'lucide-react'
import { api } from '../api/client'
import type { KbDocument } from '../api/types'
import { Chip, Screen, Segmented, SkeletonList, useAsync, useToast } from '../components/ui'
import { haptic } from '../lib/bridge'

const KIND_ICON = { pdf: FileText, pptx: Presentation, xlsx: FileSpreadsheet, answer: MessageSquareText }
const KIND_LABEL = { pdf: 'PDF', pptx: 'PPTX', xlsx: 'Сценарий xlsx', answer: 'Ответ HR' }

function DocRow({ d }: { d: KbDocument }) {
  const Icon = KIND_ICON[d.kind]
  return (
    <div className="cell" style={{ ['--sep-inset' as string]: '64px', alignItems: 'flex-start' }}>
      <span className={`doc-icon ${d.kind}`}><Icon /></span>
      <div className="cell-body">
        <div className="cell-title" style={{ fontSize: 16, lineHeight: 1.3 }}>{d.title}</div>
        <div className="cell-meta num">
          {KIND_LABEL[d.kind]} · {new Date(d.version_date).toLocaleDateString('ru-RU')} · {d.kind === 'answer' ? (d.site ?? 'все площадки') : `${d.fragments} фрагм.`}
        </div>
        <div className="chips">
          {d.status === 'confirmed' ? <Chip tone="success">Подтверждён</Chip> : <Chip tone="warning">Черновик HR</Chip>}
          {d.trade_secret && <Chip tone="danger" icon={<Lock />}>КТ</Chip>}
        </div>
      </div>
    </div>
  )
}

export default function Kb() {
  const toast = useToast()
  const [tab, setTab] = useState<'docs' | 'answers'>('docs')
  const { data, loading } = useAsync(() => api.documents(), [])
  const list = (data ?? []).filter((d) => (tab === 'docs' ? d.kind !== 'answer' : d.kind === 'answer'))
  const total = (data ?? []).filter((d) => d.kind !== 'answer').reduce((s, d) => s + d.fragments, 0)

  return (
    <Screen
      title="База"
      large
      right={
        <button
          className="nav-btn"
          aria-label="Загрузить документ"
          onClick={() => { haptic.tap(); toast('info', 'Загрузка PDF, PPTX и xlsx — в следующей версии') }}
        >
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
          { value: 'answers', label: 'Ответы HR' },
        ]}
      />
      {loading && !data && <SkeletonList rows={4} />}
      {data && (
        <section className="section">
          <div className="section-header">
            <span>{tab === 'docs' ? 'Документы базы' : 'Ответы, добавленные из обращений'}</span>
            <span className="num">{tab === 'docs' ? `${total} фрагм.` : list.length}</span>
          </div>
          <div className="group">
            {list.map((d) => <DocRow key={d.id} d={d} />)}
          </div>
          <div className="section-footer">
            {tab === 'docs'
              ? 'Бот отвечает только дословными пунктами этих документов и указывает источник. Фрагменты с меткой «КТ» не отправляются в языковую модель.'
              : 'Черновик HR начинает работать после подтверждения. До этого бот передаёт такие вопросы HR.'}
          </div>
        </section>
      )}
      <div style={{ marginTop: 24 }}>
        <button className="btn secondary" onClick={() => { haptic.tap(); toast('info', 'Загрузка PDF, PPTX и xlsx — в следующей версии') }}>
          <Upload size={20} /> Загрузить документ
        </button>
      </div>
    </Screen>
  )
}
