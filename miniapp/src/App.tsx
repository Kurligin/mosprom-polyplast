import { useCallback, useEffect, useMemo, useState } from 'react'
import { AnimatePresence, motion, useReducedMotion } from 'motion/react'
import { BookOpen, MessageSquare, MoreHorizontal, Users } from 'lucide-react'
import { api, roleParam } from './api/client'
import type { Role } from './api/types'
import { ToastProvider } from './components/ui'
import { haptic, setBackButton } from './lib/bridge'
import { NavCtx, ROUTE_TITLES, type Route, type Tab } from './lib/nav'
import Employees from './screens/Employees'
import EmployeeCard from './screens/EmployeeCard'
import NewEmployee from './screens/NewEmployee'
import Tickets from './screens/Tickets'
import TicketView from './screens/TicketView'
import Kb from './screens/Kb'
import More from './screens/More'
import { ManagerHome, ManagerEmployee } from './screens/Manager'

const TABS: { id: Tab; label: string; Icon: typeof Users }[] = [
  { id: 'employees', label: 'Новички', Icon: Users },
  { id: 'tickets', label: 'Обращения', Icon: MessageSquare },
  { id: 'kb', label: 'База', Icon: BookOpen },
  { id: 'more', label: 'Ещё', Icon: MoreHorizontal },
]

const HIDE_TABBAR: Route['name'][] = ['ticket', 'new-employee']

function renderRoute(r: Route) {
  switch (r.name) {
    case 'employees': return <Employees />
    case 'employee': return <EmployeeCard id={r.id} />
    case 'new-employee': return <NewEmployee />
    case 'tickets': return <Tickets />
    case 'ticket': return <TicketView id={r.id} />
    case 'kb': return <Kb />
    case 'more': return <More />
    case 'manager': return <ManagerHome />
    case 'manager-employee': return <ManagerEmployee id={r.id} />
  }
}

type Dir = 1 | -1 | 0

export default function App() {
  const [role, setRole] = useState<Role | null>(roleParam === 'manager' ? 'manager' : null)
  useEffect(() => {
    api.me().then((me) => setRole(me.role)).catch(() => setRole('hr'))
  }, [])
  if (!role) return <div className="app" />
  return (
    <ToastProvider>
      <Shell role={role} />
    </ToastProvider>
  )
}

function Shell({ role }: { role: Role }) {
  const isManager = role === 'manager'
  const [tab, setTab] = useState<Tab>('employees')
  const [stacks, setStacks] = useState<Record<Tab, Route[]>>(() => ({
    employees: [isManager ? { name: 'manager' } : { name: 'employees' }],
    tickets: [{ name: 'tickets' }],
    kb: [{ name: 'kb' }],
    more: [{ name: 'more' }],
  }))
  const [dir, setDir] = useState<Dir>(0)
  const [badge, setBadge] = useState(0)
  const reduce = useReducedMotion()

  const refreshBadge = useCallback(() => {
    if (isManager) return
    api.tickets('open').then((t) => setBadge(t.length)).catch(() => {})
  }, [isManager])
  useEffect(refreshBadge, [refreshBadge])

  const stack = stacks[tab]
  const top = stack[stack.length - 1]

  const push = useCallback((r: Route) => {
    setDir(1)
    setStacks((s) => ({ ...s, [tab]: [...s[tab], r] }))
  }, [tab])
  const pop = useCallback(() => {
    setDir(-1)
    setStacks((s) => (s[tab].length > 1 ? { ...s, [tab]: s[tab].slice(0, -1) } : s))
  }, [tab])

  useEffect(() => {
    setBackButton(stack.length > 1 ? pop : null)
  }, [stack.length, pop])

  const nav = useMemo(
    () => ({ push, pop, refreshBadge, backLabel: stack.length > 1 ? ROUTE_TITLES[stack[stack.length - 2].name] : '' }),
    [push, pop, refreshBadge, stack],
  )

  const switchTab = (t: Tab) => {
    haptic.select()
    if (t === tab) {
      // повторное нажатие — к корню вкладки
      if (stack.length > 1) {
        setDir(-1)
        setStacks((s) => ({ ...s, [t]: [s[t][0]] }))
      }
      return
    }
    setDir(0)
    setTab(t)
  }

  const variants = {
    initial: (d: Dir) => (reduce || d === 0 ? { opacity: d === 0 ? 1 : 0, x: 0 } : { x: d > 0 ? '100%' : '-28%', zIndex: d > 0 ? 2 : 1 }),
    animate: { x: 0, opacity: 1 },
    exit: (d: Dir) =>
      d === 0
        ? { opacity: 0, transition: { duration: 0 } }
        : reduce
          ? { opacity: 0 }
          : { x: d > 0 ? '-28%' : '100%', zIndex: d > 0 ? 1 : 2 },
  }

  const showTabbar = !isManager && !HIDE_TABBAR.includes(top.name)

  return (
    <NavCtx.Provider value={nav}>
      <div className="app">
        <AnimatePresence initial={false} custom={dir}>
          <motion.div
            key={`${tab}:${stack.length}:${top.name}:${'id' in top ? top.id : ''}`}
            className="layer"
            custom={dir}
            variants={variants}
            initial="initial"
            animate="animate"
            exit="exit"
            transition={reduce ? { duration: 0.2 } : { type: 'spring', bounce: 0, duration: 0.35 }}
          >
            {renderRoute(top)}
          </motion.div>
        </AnimatePresence>

        <AnimatePresence initial={false}>
          {showTabbar && (
            <motion.nav
              className="tabbar"
              aria-label="Разделы"
              initial={reduce ? { opacity: 0 } : { y: '100%' }}
              animate={reduce ? { opacity: 1 } : { y: 0 }}
              exit={reduce ? { opacity: 0 } : { y: '100%' }}
              transition={{ type: 'spring', bounce: 0, duration: 0.3 }}
            >
              <div className="tabbar-row" role="tablist">
                {TABS.map(({ id, label, Icon }) => (
                  <button key={id} className="tab" role="tab" aria-selected={tab === id} onClick={() => switchTab(id)}>
                    <span className="tab-icon">
                      <Icon />
                      {id === 'tickets' && badge > 0 && <span className="tab-badge num">{badge}</span>}
                    </span>
                    {label}
                  </button>
                ))}
              </div>
            </motion.nav>
          )}
        </AnimatePresence>
      </div>
    </NavCtx.Provider>
  )
}
