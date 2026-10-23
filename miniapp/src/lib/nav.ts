import { createContext, useContext } from 'react'

export type Route =
  | { name: 'employees' }
  | { name: 'employee'; id: number }
  | { name: 'new-employee' }
  | { name: 'tickets' }
  | { name: 'ticket'; id: number }
  | { name: 'kb' }
  | { name: 'upload-doc' }
  | { name: 'scenario' }
  | { name: 'more' }
  | { name: 'manager' }
  | { name: 'manager-employee'; id: number }
  | { name: 'analytics' }

export type Tab = 'employees' | 'tickets' | 'analytics' | 'kb' | 'more'

export type Nav = {
  push: (r: Route) => void
  pop: () => void
  backLabel: string
  refreshBadge: () => void
  /** Экран сейчас наверху стека и его вкладка открыта */
  active: boolean
}

export const NavCtx = createContext<Nav>({ push: () => {}, pop: () => {}, backLabel: 'Назад', refreshBadge: () => {}, active: true })
export const useNav = () => useContext(NavCtx)

export const ROUTE_TITLES: Record<Route['name'], string> = {
  employees: 'Новички',
  employee: 'Новичок',
  'new-employee': 'Новичок',
  tickets: 'Обращения',
  ticket: 'Обращение',
  kb: 'База',
  'upload-doc': 'Документ',
  scenario: 'Сценарий',
  more: 'Ещё',
  manager: 'Мои новички',
  'manager-employee': 'Новичок',
  analytics: 'Аналитика',
}
