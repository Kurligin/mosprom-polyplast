import { createContext, useCallback, useContext, useEffect, useId, useRef, useState, type ReactNode } from 'react'
import { createPortal } from 'react-dom'
import { AnimatePresence, motion, useDragControls, useReducedMotion, type PanInfo } from 'motion/react'
import { AlertTriangle, CheckCircle2, ChevronLeft, ChevronRight, CloudOff, FlaskConical, Info, Paperclip, RotateCw, SearchX, Lock } from 'lucide-react'
import { ApiError, isDemoData, toApiError } from '../api/client'
import { haptic, hasNativeBack } from '../lib/bridge'
import { useNav } from '../lib/nav'

/* ---------- Данные ---------- */

/** true, только если value держится дольше delay: скелетон не мигает на быстрых ответах */
export function useDelayed(value: boolean, delay = 300) {
  const [on, setOn] = useState(false)
  useEffect(() => {
    if (!value) {
      setOn(false)
      return
    }
    const t = window.setTimeout(() => setOn(true), delay)
    return () => window.clearTimeout(t)
  }, [value, delay])
  return on
}

/**
 * Загрузка данных экрана.
 * - demo: данные пришли из демо (сервер недоступен или эндпоинт не готов);
 * - showSkeleton: загрузка идёт дольше 300 мс и данных ещё нет;
 * - когда экран снова становится активным (вернулись назад, сменили вкладку), данные тихо обновляются.
 */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[] = []) {
  const { active } = useNav()
  const [data, setDataState] = useState<T | null>(null)
  const [demo, setDemo] = useState(false)
  const [error, setError] = useState<ApiError | null>(null)
  const [loading, setLoading] = useState(true)
  const [tick, setTick] = useState(0)
  const fnRef = useRef(fn)
  fnRef.current = fn

  useEffect(() => {
    let alive = true
    setLoading(true)
    fnRef.current()
      .then((d) => {
        if (!alive) return
        setDataState(d)
        setDemo(isDemoData(d))
        setError(null)
      })
      .catch((e: unknown) => alive && setError(toApiError(e)))
      .finally(() => alive && setLoading(false))
    return () => {
      alive = false
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [...deps, tick])

  const wasActive = useRef(active)
  useEffect(() => {
    if (active && !wasActive.current) setTick((t) => t + 1)
    wasActive.current = active
  }, [active])

  const reload = useCallback(() => setTick((t) => t + 1), [])
  const setData = useCallback((d: T | null) => setDataState(d), [])
  const showSkeleton = useDelayed(loading && data === null, 300)
  // ошибку показываем, только если показать нечего
  return { data, setData, demo, error: data === null ? error : null, loading, reload, showSkeleton }
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
  demo?: boolean
  /** Заголовок в шапке появляется после прокрутки на столько пикселей (на экране уже есть крупный заголовок) */
  revealTitleAt?: number
}

export function Screen({ title, large, back, right, children, noTabbar, bottom, bottomPad, demo, revealTitleAt }: ScreenProps) {
  const ref = useRef<HTMLDivElement>(null)
  // фон шапки появляется, как только контент уходит под неё; заголовок — когда скрылся крупный заголовок
  const [bg, setBg] = useState(false)
  const [titleShown, setTitleShown] = useState(false)
  const bgAt = large ? 36 : 4
  const titleAt = large ? 36 : revealTitleAt ?? 0
  useEffect(() => {
    const el = ref.current
    if (!el) return
    const onScroll = () => {
      setBg(el.scrollTop > bgAt)
      setTitleShown(el.scrollTop > titleAt)
    }
    onScroll()
    el.addEventListener('scroll', onScroll, { passive: true })
    return () => el.removeEventListener('scroll', onScroll)
  }, [bgAt, titleAt])
  const solid = !large && revealTitleAt === undefined
  return (
    <>
      <div
        ref={ref}
        className={`screen${noTabbar ? ' no-tabbar' : ''}`}
        style={bottomPad ? { paddingBottom: `calc(${bottomPad}px + var(--safe-bottom))` } : undefined}
      >
        <header className="navbar" data-bg={solid || bg} data-title={solid || titleShown}>
          <div className="navbar-row">
            <div className="navbar-left">
              {back && !hasNativeBack() && (
                <button className="nav-btn back" onClick={() => { haptic.tap(); back.onClick() }} aria-label={`Назад: ${back.label || 'назад'}`}>
                  <ChevronLeft size={28} />
                  <span>{back.label && back.label.length <= 8 ? back.label : 'Назад'}</span>
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
        <div className="content">
          {!large && demo && (
            <div className="demo-line">
              <DemoBadge />
            </div>
          )}
          {children}
        </div>
      </div>
      {bottom}
    </>
  )
}

export function DemoBadge() {
  return (
    <span className="demo-badge" title="Сервер не ответил или этот раздел ещё не готов. Данные синтетические">
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
  disabled?: boolean
}

export function Cell({ title, sub, meta, value, left, onClick, chevron, children, className = '', inset, strong, disabled }: CellProps) {
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
      <button className={`cell ${className}`} style={style} disabled={disabled} onClick={() => { haptic.tap(); onClick() }}>
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

export function Progress({ value, thick, tone, label }: { value: number; thick?: boolean; tone?: string; label?: string }) {
  const v = Math.max(0, Math.min(1, Number.isFinite(value) ? value : 0))
  return (
    <div
      className={`progress${thick ? ' thick' : ''}`}
      role="progressbar"
      aria-label={label}
      aria-valuenow={Math.round(v * 100)}
      aria-valuemin={0}
      aria-valuemax={100}
    >
      <span style={{ transform: `scaleX(${v})`, background: tone }} />
    </div>
  )
}

export function initials(name: string) {
  const [a, b] = name.trim().split(/\s+/)
  return ((a?.[0] ?? '') + (b?.[0] ?? '')).toUpperCase() || '?'
}

export function Avatar({ name, large, src }: { name: string; large?: boolean; src?: string | null }) {
  if (src) return <img className={`avatar${large ? ' lg' : ''}`} src={src} alt="" />
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

export function Switch({ checked, onChange, label, disabled }: { checked: boolean; onChange: (v: boolean) => void; label: string; disabled?: boolean }) {
  return (
    <span className="switch">
      <input
        type="checkbox"
        role="switch"
        aria-label={label}
        checked={checked}
        disabled={disabled}
        onChange={(e) => { haptic.select(); onChange(e.target.checked) }}
      />
      <span />
    </span>
  )
}

/* ---------- Пустое состояние, ошибка, скелетон ---------- */
export function Empty({ icon, title, text, action }: { icon: ReactNode; title: string; text?: ReactNode; action?: ReactNode }) {
  return (
    <div className="empty">
      {icon}
      <h3>{title}</h3>
      {text && <p>{text}</p>}
      {action}
    </div>
  )
}

const ERROR_TITLE: Record<number, string> = {
  0: 'Нет связи с сервером',
  401: 'Нужен вход через «МАКС»',
  403: 'Нет доступа',
  404: 'Ничего не нашли',
}

export function ErrorState({ error, onRetry, title }: { error: ApiError; onRetry: () => void; title?: string }) {
  const Icon = error.status === 0 ? CloudOff : error.status === 404 ? SearchX : error.status === 401 || error.status === 403 ? Lock : AlertTriangle
  return (
    <div className="empty" role="alert">
      <Icon />
      <h3>{title ?? ERROR_TITLE[error.status] ?? 'Не получилось загрузить'}</h3>
      <p>{error.message}</p>
      <button className="btn secondary small fit" onClick={() => { haptic.tap(); onRetry() }}>
        <RotateCw size={17} /> Повторить
      </button>
    </div>
  )
}

export function SkeletonList({ rows = 4 }: { rows?: number }) {
  return (
    <section className="section" aria-hidden>
      <div className="group">
        {Array.from({ length: rows }).map((_, i) => (
          <div className="cell" key={i}>
            <div className="skeleton" style={{ width: 40, height: 40, borderRadius: 20 }} />
            <div className="cell-body">
              <div className="skeleton" style={{ width: `${55 - (i % 3) * 8}%`, height: 16 }} />
              <div className="skeleton" style={{ width: `${80 - (i % 2) * 15}%`, height: 12 }} />
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

/** Скелетон карточки: аватар, имя, полоса срока, пара секций */
export function SkeletonCard() {
  return (
    <div aria-hidden>
      <div className="hero">
        <div className="skeleton" style={{ width: 64, height: 64, borderRadius: 32 }} />
        <div className="skeleton" style={{ width: 180, height: 22, marginTop: 6 }} />
        <div className="skeleton" style={{ width: 220, height: 14 }} />
      </div>
      <div className="probation">
        <div className="skeleton" style={{ width: '60%', height: 16 }} />
        <div className="skeleton" style={{ width: '100%', height: 6, marginTop: 10 }} />
      </div>
      <SkeletonList rows={3} />
    </div>
  )
}

/* ---------- Выбор файла ---------- */
export function fmtSize(bytes: number) {
  if (bytes < 1024) return `${bytes} Б`
  if (bytes < 1024 * 1024) return `${Math.round(bytes / 1024)} КБ`
  return `${(bytes / 1024 / 1024).toFixed(1).replace('.', ',')} МБ`
}

/** Ячейка «Выбрать файл». Нативный input прячется под ячейкой, чтобы работал и в WebView «МАКС» */
export function FilePick({
  accept, file, onPick, label, hint, disabled,
}: { accept: string; file: File | null; onPick: (f: File | null) => void; label: string; hint: string; disabled?: boolean }) {
  return (
    <label className={`cell file-pick${disabled ? ' disabled' : ''}`}>
      <span className="cell-icon" style={{ background: 'var(--primary)' }}><Paperclip /></span>
      <div className="cell-body">
        <div className="cell-title clamp-1">{file ? file.name : label}</div>
        <div className="cell-meta">{file ? fmtSize(file.size) : hint}</div>
      </div>
      <span className="cell-value file-pick-action">{file ? 'Заменить' : 'Выбрать'}</span>
      <input
        type="file"
        accept={accept}
        disabled={disabled}
        onChange={(e) => {
          const f = e.target.files?.[0] ?? null
          if (f) haptic.select()
          onPick(f)
          e.target.value = ''
        }}
      />
    </label>
  )
}

/* ---------- Лист снизу ---------- */
export function Sheet({ open, onClose, children, label }: { open: boolean; onClose: () => void; children: ReactNode; label: string }) {
  const reduce = useReducedMotion()
  const controls = useDragControls()
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
            dragListener={false}
            dragControls={controls}
            dragConstraints={{ top: 0, bottom: 0 }}
            dragElastic={{ top: 0.04, bottom: 1 }}
            onDragEnd={onDragEnd}
          >
            {/* тянуть лист можно за ручку: содержимое листа прокручивается как обычно */}
            <div className="sheet-handle" onPointerDown={(e) => controls.start(e)}>
              <div className="sheet-grabber" />
            </div>
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
    if (kind === 'warning') haptic.warning()
    setToast({ id: Date.now(), kind, text })
    window.clearTimeout(timer.current)
    // ошибки и длинные тексты висят дольше: их надо успеть прочитать
    timer.current = window.setTimeout(() => setToast(null), kind === 'error' || text.length > 60 ? 4200 : 2600)
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
              role={toast.kind === 'error' ? 'alert' : 'status'}
              onClick={() => setToast(null)}
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
const parse = (s: string | null | undefined) => {
  if (!s) return null
  const d = new Date(s.length === 10 ? `${s}T00:00:00` : s)
  return Number.isNaN(d.getTime()) ? null : d
}
export function fmtDate(isoDate: string | null | undefined) {
  const d = parse(isoDate)
  return d ? `${d.getDate()}\u00A0${MONTHS[d.getMonth()]}` : '' // неразрывный пробел: «21 окт» не рвётся
}
export function fmtTime(isoDate: string) {
  const d = parse(isoDate)
  return d ? d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' }) : ''
}
export function fmtAgo(isoDate: string) {
  const d = parse(isoDate)
  if (!d) return ''
  const diff = (Date.now() - d.getTime()) / 60000
  if (diff < 1) return 'только что'
  if (diff < 60) return `${Math.round(diff)} мин назад`
  if (diff < 60 * 24) return `${Math.round(diff / 60)} ч назад`
  return fmtDate(isoDate)
}
export function fmtDue(isoDate: string) {
  const d = parse(isoDate)
  if (!d) return { text: '', overdue: false }
  const diffH = (d.getTime() - Date.now()) / 3600000
  if (diffH < 0) return { text: `просрочено на ${Math.max(1, Math.round(-diffH))} ч`, overdue: true }
  if (diffH < 1) return { text: `меньше часа`, overdue: true }
  return { text: `осталось ${Math.round(diffH)} ч`, overdue: false }
}
/** 1 фрагмент, 2 фрагмента, 5 фрагментов */
export function plural(n: number, forms: [string, string, string]) {
  const a = Math.abs(n) % 100
  const b = a % 10
  if (a > 10 && a < 20) return forms[2]
  if (b > 1 && b < 5) return forms[1]
  if (b === 1) return forms[0]
  return forms[2]
}
/**
 * «Сегодня» новичка, YYYY-MM-DD: первый рабочий день + (день адаптации − 1).
 * Учитывает и выход в выходной, и машину времени в демо: бэкенд считает day с ней.
 */
export function employeeToday(e: { first_day: string; day: number }) {
  const d = new Date(`${e.first_day}T00:00:00Z`)
  d.setUTCDate(d.getUTCDate() + e.day - 1)
  return d.toISOString().slice(0, 10)
}
export function dayLabel(day: number, total: number) {
  if (day < 1) return day === 0 ? 'выходит завтра' : `до выхода ${1 - day} ${plural(1 - day, ['день', 'дня', 'дней'])}`
  if (day > total) return 'срок закончился'
  return `день ${day} из ${total}`
}
