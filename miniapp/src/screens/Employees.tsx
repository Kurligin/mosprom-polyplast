import { useMemo, useState } from 'react'
import { motion, useReducedMotion } from 'motion/react'
import { ClipboardList, MessageSquare, Plus, Search, ShieldAlert, UserPlus, UserX, X } from 'lucide-react'
import { api } from '../api/client'
import type { EmployeeShort } from '../api/types'
import { Avatar, Chip, Empty, ErrorState, Progress, Screen, SkeletonList, dayLabel, useAsync } from '../components/ui'
import { haptic } from '../lib/bridge'
import { useNav } from '../lib/nav'
import { useSites } from '../lib/sites'

export function EmployeeChips({ e }: { e: EmployeeShort }) {
  const chips = []
  if (e.red_flag) chips.push(<Chip key="f" tone="danger" icon={<ShieldAlert />}>Красный флаг</Chip>)
  if (e.open_tickets > 0) chips.push(<Chip key="t" tone="primary" icon={<MessageSquare />}>Обращение{e.open_tickets > 1 ? ` · ${e.open_tickets}` : ''}</Chip>)
  if (e.pending_surveys > 0) chips.push(<Chip key="s" tone="warning" icon={<ClipboardList />}>Опрос не пройден{e.pending_surveys > 1 ? ` · ${e.pending_surveys}` : ''}</Chip>)
  if (!e.connected) chips.push(<Chip key="c" icon={<UserX />}>Не подключился</Chip>)
  return chips.length ? <div className="chips">{chips}</div> : null
}

export function EmployeeRow({ e, onClick }: { e: EmployeeShort; onClick: () => void }) {
  const sub = e.current_step || e.position
  return (
    <button className="cell" style={{ ['--sep-inset' as string]: '68px', alignItems: 'flex-start' }} onClick={() => { haptic.tap(); onClick() }}>
      <Avatar name={e.full_name} />
      <div className="cell-body">
        <div className="cell-title strong clamp-2">{e.full_name}</div>
        <div className="cell-meta clamp-1">
          <span className="num">{dayLabel(e.day, e.probation_days)}</span>
          {sub ? ` · ${sub}` : ''}
        </div>
        <Progress value={e.day / e.probation_days} label="Испытательный срок" />
        <EmployeeChips e={e} />
      </div>
    </button>
  )
}

export default function Employees() {
  const nav = useNav()
  const reduce = useReducedMotion()
  const [q, setQ] = useState('')
  const { data, demo, error, reload, showSkeleton } = useAsync(() => api.employees(), [])
  const { sites, name } = useSites()

  const groups = useMemo(() => {
    const s = q.trim().toLowerCase()
    const list = (data ?? []).filter((e) => !s || e.full_name.toLowerCase().includes(s) || e.position.toLowerCase().includes(s))
    // порядок площадок из справочника, незнакомые коды в конце
    const codes = [...sites.map((x) => x.code), ...new Set(list.map((e) => e.site))].filter((c, i, a) => a.indexOf(c) === i)
    return codes.map((code) => ({ code, items: list.filter((e) => e.site === code) })).filter((g) => g.items.length)
  }, [data, q, sites])

  const addNew = () => { haptic.tap(); nav.push({ name: 'new-employee' }) }

  return (
    <Screen
      title="Новички"
      large
      demo={demo}
      right={
        <button className="nav-btn" aria-label="Завести новичка" onClick={addNew}>
          <Plus size={26} />
        </button>
      }
    >
      {(data?.length ?? 0) > 0 && (
        <label className="search">
          <Search size={17} />
          <input
            type="search"
            placeholder="Имя или должность"
            value={q}
            onChange={(e) => setQ(e.target.value)}
            enterKeyHint="search"
            aria-label="Поиск новичка"
          />
          {q && (
            <button className="search-clear" aria-label="Очистить поиск" onClick={() => setQ('')}>
              <X size={14} />
            </button>
          )}
        </label>
      )}

      {showSkeleton && <SkeletonList rows={6} />}
      {error && <ErrorState error={error} onRetry={reload} />}

      {data && data.length === 0 && (
        <Empty
          icon={<UserPlus />}
          title="Пока нет новичков"
          text="Заведите первого: Корсик даст ссылку и QR-код для оффера, а дальше будет вести человека сам."
          action={<button className="btn small fit" onClick={addNew}><Plus size={18} /> Завести новичка</button>}
        />
      )}
      {data && data.length > 0 && groups.length === 0 && (
        <Empty icon={<Search />} title="Никого не нашли" text="Проверьте, как написано имя, или поищите по должности." />
      )}

      {groups.map((g, gi) => (
        <motion.section
          className="section"
          key={g.code}
          initial={reduce ? false : { opacity: 0, transform: 'translateY(8px)' }}
          animate={{ opacity: 1, transform: 'translateY(0px)' }}
          transition={{ duration: 0.25, delay: gi * 0.03, ease: [0.23, 1, 0.32, 1] }}
        >
          <div className="section-header">
            <span>{name(g.code)}</span>
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
