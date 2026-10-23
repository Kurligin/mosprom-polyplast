import { useCallback, useEffect, useMemo, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { BookOpen, ChartColumnIncreasing, Lock, MessageSquare, MoreHorizontal, ShieldOff, Users } from 'lucide-react'
import { api, toApiError, type ApiError } from './api/client'
import type { Role } from './api/types'
import { ErrorState, SkeletonList, ToastProvider, useDelayed } from './components/ui'
import { haptic, isInMax, setBackButton, startParam } from './lib/bridge'
import { NavCtx, ROUTE_TITLES, type Nav, type Route, type Tab } from './lib/nav'
import Employees from './screens/Employees'
import EmployeeCard from './screens/EmployeeCard'
import NewEmployee from './screens/NewEmployee'
import Tickets from './screens/Tickets'
import TicketView from './screens/TicketView'
import Kb from './screens/Kb'
import UploadDoc from './screens/UploadDoc'
import ScenarioImport from './screens/ScenarioImport'
import More from './screens/More'
import { ManagerHome, ManagerEmployee } from './screens/Manager'
import Analytics from './screens/Analytics'

const TAB_INFO: Record<Tab, { label: string; Icon: typeof Users }> = {
  analytics: { label: 'Аналитика', Icon: ChartColumnIncreasing },
  employees: { label: 'Новички', Icon: Users },
  tickets: { label: 'Обращения', Icon: MessageSquare },
  kb: { label: 'База', Icon: BookOpen },
  more: { label: 'Ещё', Icon: MoreHorizontal },
}

/** Руководителю HR аналитика — главный экран; HR — отдельная вкладка рядом с обращениями */
const tabsFor = (role: Role): Tab[] =>
  role === 'manager'
    ? ['employees']
    : role === 'head'
      ? ['analytics', 'employees', 'tickets', 'kb', 'more']
      : ['employees', 'tickets', 'analytics', 'kb', 'more']

const HIDE_TABBAR: Route['name'][] = ['ticket', 'new-employee', 'upload-doc', 'scenario']

function renderRoute(r: Route) {
  switch (r.name) {
    case 'employees': return <Employees />
    case 'employee': return <EmployeeCard id={r.id} />
    case 'new-employee': return <NewEmployee />
    case 'tickets': return <Tickets />
    case 'ticket': return <TicketView id={r.id} />
    case 'kb': return <Kb />
    case 'upload-doc': return <UploadDoc />
    case 'scenario': return <ScenarioImport />
    case 'more': return <More />
    case 'manager': return <ManagerHome />
    case 'manager-employee': return <ManagerEmployee id={r.id} />
    case 'analytics': return <Analytics />
  }
}

/** Недоступные слои: не ловят фокус и касания, скринридер их не читает */
const inert = (on: boolean): { 'aria-hidden'?: boolean } => (on ? ({ inert: '', 'aria-hidden': true } as { 'aria-hidden': boolean }) : {})

type Entry = { key: number; route: Route }
let seq = 0
const entry = (route: Route): Entry => ({ key: ++seq, route })

export default function App() {
  const [state, setState] = useState<{ role: Role } | { error: ApiError } | null>(null)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    setState(null)
    api.me().then((me) => setState({ role: me.role })).catch((e) => setState({ error: toApiError(e) }))
  }, [tick])
  const slow = useDelayed(state === null, 300)

  if (!state) return <div className="app">{slow && <LaunchSkeleton />}</div>
  if ('error' in state) return <AccessScreen error={state.error} onRetry={() => setTick((t) => t + 1)} />
  return (
    <ToastProvider>
      <Shell role={state.role} />
    </ToastProvider>
  )
}

function LaunchSkeleton() {
  return (
    <div className="screen" aria-busy="true" aria-label="Загрузка">
      <div className="launch">
        <div className="skeleton" style={{ width: 140, height: 30, borderRadius: 8 }} />
        <div className="skeleton" style={{ width: '100%', height: 36, borderRadius: 10, marginTop: 16 }} />
      </div>
      <div className="content">
        <SkeletonList rows={6} />
      </div>
    </div>
  )
}

function AccessScreen({ error, onRetry }: { error: ApiError; onRetry: () => void }) {
  const noAuth = error.status === 401
  const forbidden = error.status === 403
  // внутри «МАКС» 401 значит, что подпись не сошлась или устарела: помогает открыть панель заново
  const inMax = isInMax()
  return (
    <div className="app">
      <div className="screen no-tabbar access">
        {noAuth || forbidden ? (
          <div className="empty">
            {noAuth ? <Lock /> : <ShieldOff />}
            <h3>{noAuth ? (inMax ? 'Не получилось подтвердить вход' : 'Откройте панель в «МАКС»') : 'Нет доступа'}</h3>
            <p>
              {noAuth
                ? inMax
                  ? 'Закройте панель и откройте её снова из чата с Корсиком.'
                  : 'Панель работает внутри чата с Корсиком. Нажмите в боте кнопку «Открыть панель».'
                : error.message && error.message !== 'Нет доступа к этому разделу'
                  ? error.message
                  : 'Панель видят HR и руководители. Если доступ нужен вам, напишите в HR.'}
            </p>
            <button className="btn secondary small fit" onClick={onRetry}>Повторить</button>
          </div>
        ) : (
          <ErrorState error={error} onRetry={onRetry} />
        )}
      </div>
    </div>
  )
}

/** Куда открыть панель по кнопке из бота: обращение или карточка новичка */
function deepLink(isManager: boolean): { tab: Tab; route: Route } | null {
  const m = /^(ticket|employee)_(\d+)$/.exec(startParam() ?? '')
  if (!m) return null
  const id = Number(m[2])
  if (m[1] === 'ticket') return isManager ? null : { tab: 'tickets', route: { name: 'ticket', id } }
  return { tab: 'employees', route: isManager ? { name: 'manager-employee', id } : { name: 'employee', id } }
}

function Shell({ role }: { role: Role }) {
  const isManager = role === 'manager'
  const tabs = tabsFor(role)
  const [link] = useState(() => deepLink(isManager))
  const [tab, setTab] = useState<Tab>(link?.tab ?? tabs[0])
  const [visited, setVisited] = useState<Tab[]>([link?.tab ?? tabs[0]])
  const [stacks, setStacks] = useState<Record<Tab, Entry[]>>(() => {
    const s: Record<Tab, Entry[]> = {
      employees: [entry(isManager ? { name: 'manager' } : { name: 'employees' })],
      tickets: [entry({ name: 'tickets' })],
      analytics: [entry({ name: 'analytics' })],
      kb: [entry({ name: 'kb' })],
      more: [entry({ name: 'more' })],
    }
    if (link) s[link.tab] = [...s[link.tab], entry(link.route)]
    return s
  })
  const [badge, setBadge] = useState(0)
  const reduce = !!useReducedMotion()

  const refreshBadge = useCallback(() => {
    if (isManager) return
    api.tickets('open').then((t) => setBadge(t.length)).catch(() => {})
  }, [isManager])
  useEffect(refreshBadge, [refreshBadge])

  const push = useCallback((t: Tab, r: Route) => {
    setStacks((s) => ({ ...s, [t]: [...s[t], entry(r)] }))
  }, [])
  const pop = useCallback((t: Tab) => {
    setStacks((s) => (s[t].length > 1 ? { ...s, [t]: s[t].slice(0, -1) } : s))
  }, [])

  const stack = stacks[tab]
  const top = stack[stack.length - 1].route

  useEffect(() => {
    setBackButton(stack.length > 1 ? () => pop(tab) : null)
  }, [stack.length, tab, pop])

  const switchTab = (t: Tab) => {
    haptic.select()
    if (t === tab) {
      // повторное нажатие: к корню вкладки
      if (stack.length > 1) setStacks((s) => ({ ...s, [t]: [s[t][0]] }))
      return
    }
    setVisited((v) => (v.includes(t) ? v : [...v, t]))
    setTab(t)
  }

  const showTabbar = !isManager && !HIDE_TABBAR.includes(top.name)

  return (
    <div className="app">
      {tabs
        .filter((t) => visited.includes(t))
        .map((t) => (
          <div key={t} className="pane" data-visible={t === tab} {...inert(t !== tab)}>
            <AnimatePresence initial={false}>
              {stacks[t].map((e, i) => (
                <Layer
                  key={e.key}
                  route={e.route}
                  index={i}
                  depth={stacks[t].length - 1 - i}
                  backLabel={i > 0 ? ROUTE_TITLES[stacks[t][i - 1].route.name] : ''}
                  active={t === tab && i === stacks[t].length - 1}
                  tab={t}
                  push={push}
                  pop={pop}
                  refreshBadge={refreshBadge}
                  reduce={reduce}
                />
              ))}
            </AnimatePresence>
          </div>
        ))}

      <AnimatePresence initial={false}>
        {showTabbar && (
          <motion.nav
            className="tabbar"
            aria-label="Разделы"
            initial={reduce ? { opacity: 0 } : { transform: 'translateY(100%)' }}
            animate={reduce ? { opacity: 1 } : { transform: 'translateY(0%)' }}
            exit={reduce ? { opacity: 0 } : { transform: 'translateY(100%)' }}
            transition={{ type: 'spring', bounce: 0, duration: 0.3 }}
          >
            <div className="tabbar-row" role="tablist" style={{ gridTemplateColumns: `repeat(${tabs.length}, 1fr)` }}>
              {tabs.map((id) => {
                const { label, Icon } = TAB_INFO[id]
                return (
                  <button key={id} className="tab" role="tab" aria-selected={tab === id} onClick={() => switchTab(id)}>
                    <span className="tab-icon">
                      <Icon />
                      {id === 'tickets' && badge > 0 && <span className="tab-badge num">{badge > 99 ? '99+' : badge}</span>}
                    </span>
                    {label}
                  </button>
                )
              })}
            </div>
          </motion.nav>
        )}
      </AnimatePresence>
    </div>
  )
}

type LayerProps = {
  route: Route
  index: number
  depth: number
  backLabel: string
  active: boolean
  tab: Tab
  push: (t: Tab, r: Route) => void
  pop: (t: Tab) => void
  refreshBadge: () => void
  reduce: boolean
}

/** Экран в стеке. Нижние экраны остаются смонтированными: скролл и состояние на месте */
function Layer({ route, index, depth, backLabel, active, tab, push, pop, refreshBadge, reduce }: LayerProps) {
  const nav = useMemo<Nav>(
    () => ({ push: (r) => push(tab, r), pop: () => pop(tab), backLabel, refreshBadge, active }),
    [push, pop, tab, backLabel, refreshBadge, active],
  )
  const variants = reduce
    ? { enter: { opacity: 0 }, top: { opacity: 1 }, under: { opacity: 1 }, exit: { opacity: 0 } }
    : {
        enter: { transform: 'translateX(100%)' },
        top: { transform: 'translateX(0%)' },
        under: { transform: 'translateX(-28%)' },
        exit: { transform: 'translateX(100%)' },
      }
  return (
    <motion.div
      className="layer"
      data-root={index === 0}
      style={{ zIndex: index + 1, visibility: depth >= 2 ? 'hidden' : undefined }}
      variants={variants}
      initial="enter"
      animate={depth === 0 ? 'top' : 'under'}
      exit="exit"
      transition={reduce ? { duration: 0.2 } : { type: 'spring', bounce: 0, duration: 0.35 }}
      {...inert(depth > 0)}
    >
      <NavCtx.Provider value={nav}>{renderRoute(route)}</NavCtx.Provider>
    </motion.div>
  )
}
