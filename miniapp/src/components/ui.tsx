import { createContext, useCallback, useContext, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion, useReducedMotion, type PanInfo } from 'motion/react'
import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, Info, FlaskConical } from 'lucide-react'
import { isDemo, onDemoChange } from '../api/client'
import { haptic, hasNativeBack } from '../lib/bridge'

/* ---------- Данные ---------- */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const [data, setData] = useState<T | null>(null)
  const [error, setError] = useState<string | null>(null)
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    let alive = true
    setLoading(true)
    fn()
      .then((d) => alive && (setData(d), setError(null)))
      .catch((e: unknown) => alive && setError(e instanceof Error ? e.message : 'Ошибка'))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])
  const reload = useCallback(() => setTick((t) => t + 1), [])
  return { data, setData, error, loading, reload }
}

export function useDemo() {
  const [demo, setDemo] = useState(isDemo())
  useEffect(() => onDemoChange(setDemo), [])
  return demo
}

/* ---------- Экран ---------- */
type ScreenProps = {
  title: string
  large?: boolean
  back?: { label: string; onClick: () => void }
  right?: ReactNode
  children: ReactNode
  noTabbar?: boolean
  bottom?: ReactNode
  bottomPad?: number
}

export function Screen({ title, large, back, right, children, noTabbar, bottom, bottomPad }: ScreenProps) {
  const ref = useRef<HTMLDivElement>(null)
  const [scrolled, setScrolled] = useState(false)
  const demo = useDemo()
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const onScroll = () => setScrolled(el.scrollTop > (large ? 36 : 4))
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [large])
  return (
    <>
      <div
        ref={ref}
        className={`screen${noTabbar ? ' no-tabbar' : ''}`}
        style={bottomPad ? { paddingBottom: `calc(${bottomPad}px + var(--safe-bottom))` } : undefined}
      >
        <header className="navbar" data-scrolled={scrolled} data-solid={!large}>
          <div className="navbar-row">
            <div className="navbar-left">
              {back && !hasNativeBack() && (
                <button className="nav-btn back" onClick={() => { haptic.tap(); back.onClick() }} aria-label={`Назад: ${back.label}`}>
                  <ChevronLeft size={28} />
                  <span>{back.label && back.label.length <= 7 ? back.label : 'Назад'}</span>
                </button>
              )}
            </div>
            <div className="navbar-title">{title}</div>
            <div className="navbar-right">{right}</div>
          </div>
        </header>
        {large && (
          <h1 className="large-title">
            {title}
            {demo && <DemoBadge />}
          </h1>
        )}
        <div className="content">{children}</div>
      </div>
      {bottom}
    </>
  )
}

export function DemoBadge() {
  return (
    <span className="demo-badge" title="Синтетические данные: бэкенд не подключён">
      <FlaskConical size={12} />
      демо-данные
    </span>
  )
}

/* ---------- Списки ---------- */
export function Section({ header, footer, children, aside }: { header?: ReactNode; footer?: ReactNode; aside?: ReactNode; children: ReactNode }) {
  return (
    <section className="section">
      {header && (
        <div className="section-header">
          <span>{header}</span>
          {aside && <span>{aside}</span>}
        </div>
      )}
      <div className="group">{children}</div>
      {footer && <div className="section-footer">{footer}</div>}
    </section>
  )
}

type CellProps = {
  title: ReactNode
  sub?: ReactNode
  meta?: ReactNode
  value?: ReactNode
  left?: ReactNode
  onClick?: () => void
  chevron?: boolean
  children?: ReactNode
  className?: string
  inset?: number
  strong?: boolean
}

export function Cell({ title, sub, meta, value, left, onClick, chevron, children, className = '', inset, strong }: CellProps) {
  const body = (
    <>
      {left}
      <div className="cell-body">
        <div className={`cell-title${strong ? ' strong' : ''}`}>{title}</div>
        {sub && <div className="cell-sub">{sub}</div>}
        {meta && <div className="cell-meta">{meta}</div>}
        {children}
      </div>
      {value !== undefined && <div className="cell-value">{value}</div>}
      {chevron && <ChevronRight className="chev" />}
    </>
  )
  const style = inset !== undefined ? ({ ['--sep-inset' as string]: `${inset}px` } as React.CSSProperties) : undefined
  if (onClick)
    return (
      <button className={`cell ${className}`} style={style} onClick={() => { haptic.tap(); onClick() }}>
        {body}
      </button>
    )
  return (
    <div className={`cell ${className}`} style={style}>
      {body}
    </div>
  )
}

export function Chip({ tone, icon, children }: { tone?: 'warning' | 'danger' | 'primary' | 'success'; icon?: ReactNode; children: ReactNode }) {
  return (
    <span className={`chip ${tone ?? ''}`}>
      {icon}
      {children}
    </span>
  )
}

export function Progress({ value, thick, tone }: { value: number; thick?: boolean; tone?: string }) {
  const v = Math.max(0, Math.min(1, value))
  return (
    <div className={`progress${thick ? ' thick' : ''}`} role="progressbar" aria-valuenow={Math.round(v * 100)} aria-valuemin={0} aria-valuemax={100}>
      <span style={{ transform: `scaleX(${v})`, background: tone }} />
    </div>
  )
}

export function initials(name: string) {
  const [a, b] = name.split(' ')
  return ((a?.[0] ?? '') + (b?.[0] ?? '')).toUpperCase()
}

export function Avatar({ name, large }: { name: string; large?: boolean }) {
  return <div className={`avatar${large ? ' lg' : ''}`} aria-hidden>{initials(name)}</div>
}

/* ---------- Сегмент ---------- */
export function Segmented<T extends string>({
  options, value, onChange, tall, ariaLabel,
}: { options: { value: T; label: ReactNode }[]; value: T; onChange: (v: T) => void; tall?: boolean; ariaLabel?: string }) {
  const id = useId()
  const reduce = useReducedMotion()
  return (
    <div className={`segmented${tall ? ' tall' : ''}`} role="tablist" aria-label={ariaLabel}>
      {options.map((o) => (
        <button
          key={o.value}
          role="tab"
          type="button"
          aria-selected={o.value === value}
          onClick={() => {
            if (o.value !== value) haptic.select()
            onChange(o.value)
          }}
        >
          {o.value === value && (
            <motion.span
              layoutId={`seg-${id}`}
              className="seg-thumb"
              transition={reduce ? { duration: 0 } : { type: 'spring', bounce: 0, duration: 0.3 }}
            />
          )}
          {o.label}
        </button>
      ))}
    </div>
  )
}

export function Switch({ checked, onChange, label }: { checked: boolean; onChange: (v: boolean) => void; label: string }) {
  return (
    <span className="switch">
      <input type="checkbox" role="switch" aria-label={label} checked={checked} onChange={(e) => { haptic.select(); onChange(e.target.checked) }} />
      <span />
    </span>
  )
}

/* ---------- Пустое состояние и скелетон ---------- */
export function Empty({ icon, title, text }: { icon: ReactNode; title: string; text?: string }) {
  return (
    <div className="empty">
      {icon}
      <h3>{title}</h3>
      {text && <p>{text}</p>}
    </div>
  )
}

export function SkeletonList({ rows = 4 }: { rows?: number }) {
  return (
    <section className="section">
      <div className="group">
        {Array.from({ length: rows }).map((_, i) => (
          <div className="cell" key={i}>
            <div className="skeleton" style={{ width: 40, height: 40, borderRadius: 20 }} />
            <div className="cell-body">
              <div className="skeleton" style={{ width: '55%', height: 16 }} />
              <div className="skeleton" style={{ width: '80%', height: 12 }} />
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

/* ---------- Лист снизу ---------- */
export function Sheet({ open, onClose, children, label }: { open: boolean; onClose: () => void; children: ReactNode; label: string }) {
  const reduce = useReducedMotion()
  const onDragEnd = (_: unknown, info: PanInfo) => {
    if (info.offset.y > 120 || info.velocity.y > 500) onClose()
  }
  useEffect(() => {
    if (!open) return
    const onKey = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', onKey)
    return () => window.removeEventListener('keydown', onKey)
  }, [open, onClose])
  return createPortal(
    <AnimatePresence>
      {open && (
        <>
          <motion.div
            key="scrim"
            className="scrim"
            onClick={onClose}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            exit={{ opacity: 0 }}
            transition={{ duration: 0.2 }}
          />
          <motion.div
            key="sheet"
            className="sheet"
            role="dialog"
            aria-modal="true"
            aria-label={label}
            initial={reduce ? { opacity: 0 } : { y: '100%' }}
            animate={reduce ? { opacity: 1 } : { y: 0 }}
            exit={reduce ? { opacity: 0 } : { y: '100%' }}
            transition={reduce ? { duration: 0.2 } : { type: 'spring', bounce: 0.15, duration: 0.45 }}
            drag={reduce ? false : 'y'}
            dragConstraints={{ top: 0, bottom: 0 }}
            dragElastic={{ top: 0.04, bottom: 1 }}
            onDragEnd={onDragEnd}
          >
            <div className="sheet-grabber" />
            {children}
          </motion.div>
        </>
      )}
    </AnimatePresence>,
    document.body,
  )
}

/* ---------- Тосты ---------- */
type ToastKind = 'success' | 'error' | 'info' | 'warning'
type ToastT = { id: number; kind: ToastKind; text: string }
const ToastCtx = createContext<(kind: ToastKind, text: string) => void>(() => {})
export const useToast = () => useContext(ToastCtx)

export function ToastProvider({ children }: { children: ReactNode }) {
  const [toast, setToast] = useState<ToastT | null>(null)
  const reduce = useReducedMotion()
  const timer = useRef<number | undefined>(undefined)
  const show = useCallback((kind: ToastKind, text: string) => {
    if (kind === 'success') haptic.success()
    if (kind === 'error') haptic.error()
    setToast({ id: Date.now(), kind, text })
    window.clearTimeout(timer.current)
    timer.current = window.setTimeout(() => setToast(null), 2600)
  }, [])
  const Icon = toast?.kind === 'success' ? CheckCircle2 : toast?.kind === 'error' || toast?.kind === 'warning' ? AlertTriangle : Info
  return (
    <ToastCtx.Provider value={show}>
      {children}
      <div className="toast-wrap" aria-live="polite">
        <AnimatePresence>
          {toast && (
            <motion.div
              key={toast.id}
              className={`toast ${toast.kind}`}
              initial={reduce ? { opacity: 0 } : { opacity: 0, y: -16, scale: 0.96 }}
              animate={{ opacity: 1, y: 0, scale: 1 }}
              exit={reduce ? { opacity: 0 } : { opacity: 0, y: -12, scale: 0.98 }}
              transition={{ type: 'spring', bounce: 0, duration: 0.3 }}
            >
              <Icon />
              <span>{toast.text}</span>
            </motion.div>
          )}
        </AnimatePresence>
      </div>
    </ToastCtx.Provider>
  )
}

/* ---------- Форматирование ---------- */
const MONTHS = ['янв', 'фев', 'мар', 'апр', 'мая', 'июн', 'июл', 'авг', 'сен', 'окт', 'ноя', 'дек']
export function fmtDate(isoDate: string) {
  const d = new Date(isoDate)
  return `${d.getDate()} ${MONTHS[d.getMonth()]}`
}
export function fmtTime(isoDate: string) {
  const d = new Date(isoDate)
  return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' })
}
export function fmtAgo(isoDate: string) {
  const diff = (Date.now() - new Date(isoDate).getTime()) / 60000
  if (diff < 1) return 'только что'
  if (diff < 60) return `${Math.round(diff)} мин назад`
  if (diff < 60 * 24) return `${Math.round(diff / 60)} ч назад`
  return fmtDate(isoDate)
}
export function fmtDue(isoDate: string) {
  const diffH = (new Date(isoDate).getTime() - Date.now()) / 3600000
  if (diffH < 0) return { text: `просрочено на ${Math.max(1, Math.round(-diffH))} ч`, overdue: true }
  if (diffH < 1) return { text: `срок < 1 ч`, overdue: true }
  return { text: `срок ${Math.round(diffH)} ч`, overdue: false }
}
export function dayLabel(day: number, total: number) {
  if (day < 1) return day === 0 ? 'выходит завтра' : `до выхода ${1 - day} дн.`
  return `день ${Math.min(day, total)} из ${total}`
}
