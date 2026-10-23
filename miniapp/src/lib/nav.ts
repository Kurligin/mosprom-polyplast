import { createContext, useContext } from 'react'

export type Route =
  | { name: 'employees' }
  | { name: 'employee'; id: number }
  | { name: 'new-employee' }
  | { name: 'tickets' }
  | { name: 'ticket'; id: number }
  | { name: 'kb' }
  | { name: 'more' }
  | { name: 'manager' }
  | { name: 'manager-employee'; id: number }

export type Tab = 'employees' | 'tickets' | 'kb' | 'more'

export type Nav = {
  push: (r: Route) => void
  pop: () => void
  backLabel: string
  refreshBadge: () => void
}

export const NavCtx = createContext<Nav>({ push: () => {}, pop: () => {}, backLabel: 'Назад', refreshBadge: () => {} })
export const useNav = () => useContext(NavCtx)

export const ROUTE_TITLES: Record<Route['name'], string> = {
  employees: 'Новички',
  employee: 'Новичок',
  'new-employee': 'Новичок',
  tickets: 'Обращения',
  ticket: 'Обращение',
  kb: 'База',
  more: 'Ещё',
  manager: 'Мои новички',
  'manager-employee': 'Новичок',
}
