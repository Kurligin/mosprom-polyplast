import { useMemo, useState } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { ClipboardList, MessageSquare, Plus, Search, ShieldAlert, UserX, Users } from 'lucide-react'
import { api } from '../api/client'
import { SITES } from '../api/mocks'
import type { EmployeeShort } from '../api/types'
import { Avatar, Chip, Empty, Progress, Screen, SkeletonList, dayLabel, useAsync } from '../components/ui'
import { haptic } from '../lib/bridge'
import { useNav } from '../lib/nav'

export function EmployeeChips({ e }: { e: EmployeeShort }) {
  const chips = []
  if (e.red_flag) chips.push(<Chip key="f" tone="danger" icon={<ShieldAlert />}>Красный флаг</Chip>)
  if (e.open_tickets > 0) chips.push(<Chip key="t" tone="primary" icon={<MessageSquare />}>Обращение{e.open_tickets > 1 ? ` · ${e.open_tickets}` : ''}</Chip>)
  if (e.pending_surveys > 0) chips.push(<Chip key="s" tone="warning" icon={<ClipboardList />}>Опрос не пройден{e.pending_surveys > 1 ? ` · ${e.pending_surveys}` : ''}</Chip>)
  if (!e.connected) chips.push(<Chip key="c" icon={<UserX />}>Не подключился</Chip>)
  return chips.length ? <div className="chips">{chips}</div> : null
}

export function EmployeeRow({ e, onClick, meta }: { e: EmployeeShort; onClick: () => void; meta?: string }) {
  return (
    <button className="cell" style={{ ['--sep-inset' as string]: '68px', alignItems: 'flex-start' }} onClick={() => { haptic.tap(); onClick() }}>
      <Avatar name={e.full_name} />
      <div className="cell-body">
        <div className="cell-title strong clamp-1">{e.full_name}</div>
        <div className="cell-meta clamp-1">
          <span className="num">{dayLabel(e.day, e.probation_days)}</span>
          {' · '}
          {meta ?? e.current_step ?? e.position}
        </div>
        <Progress value={e.day / e.probation_days} />
        <EmployeeChips e={e} />
      </div>
    </button>
  )
}

export default function Employees() {
  const nav = useNav()
  const reduce = useReducedMotion()
  const [q, setQ] = useState('')
  const { data, loading } = useAsync(() => api.employees(), [])

  const groups = useMemo(() => {
    const list = (data ?? []).filter((e) => {
      const s = q.trim().toLowerCase()
      return !s || e.full_name.toLowerCase().includes(s) || e.position.toLowerCase().includes(s)
    })
    return SITES.map((s) => ({ site: s, items: list.filter((e) => e.site === s.code) })).filter((g) => g.items.length)
  }, [data, q])

  return (
    <Screen
      title="Новички"
      large
      right={
        <button className="nav-btn" aria-label="Завести новичка" onClick={() => { haptic.tap(); nav.push({ name: 'new-employee' }) }}>
          <Plus size={26} />
        </button>
      }
    >
      <label className="search">
        <Search size={17} />
        <input type="search" placeholder="Поиск по ФИО или должности" value={q} onChange={(e) => setQ(e.target.value)} enterKeyHint="search" />
      </label>

      {loading && !data && <SkeletonList rows={5} />}

      {data && groups.length === 0 && (
        <Empty icon={<Users />} title={q ? 'Никого не нашли' : 'Пока нет новичков'} text={q ? 'Проверьте написание или поищите по должности.' : 'Нажмите «+», чтобы завести первого.'} />
      )}

      {groups.map((g, gi) => (
        <motion.section
          className="section"
          key={g.site.code}
          initial={reduce ? false : { opacity: 0, y: 8 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.25, delay: gi * 0.03, ease: [0.23, 1, 0.32, 1] }}
        >
          <div className="section-header">
            <span>{g.site.name}</span>
            <span className="num">{g.items.length}</span>
          </div>
          <div className="group">
            {g.items.map((e) => (
              <EmployeeRow key={e.id} e={e} onClick={() => nav.push({ name: 'employee', id: e.id })} />
            ))}
          </div>
        </motion.section>
      ))}
    </Screen>
  )
}
