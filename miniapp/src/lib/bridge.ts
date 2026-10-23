// MAX Bridge (window.WebApp). Вне «МАКС» все вызовы — безопасные заглушки.

type Haptic = {
  impactOccurred?: (style: 'light' | 'medium' | 'heavy' | 'rigid' | 'soft', disableVibrationFallback?: boolean) => void
  notificationOccurred?: (type: 'error' | 'success' | 'warning', disableVibrationFallback?: boolean) => void
  selectionChanged?: (disableVibrationFallback?: boolean) => void
}

type BackButton = {
  isVisible?: boolean
  show?: () => void
  hide?: () => void
  onClick?: (cb: () => void) => void
  offClick?: (cb: () => void) => void
}

export type MaxWebApp = {
  initData?: string
  initDataUnsafe?: { user?: { id: number; first_name?: string; last_name?: string }; start_param?: string }
  platform?: string
  version?: string
  ready?: () => void
  close?: () => void
  BackButton?: BackButton
  HapticFeedback?: Haptic
  openLink?: (url: string) => void
  openMaxLink?: (url: string) => void
  shareContent?: (params: { text?: string; link?: string }) => void
  shareMaxContent?: (params: { text?: string; link?: string }) => void
}

declare global {
  interface Window {
    WebApp?: MaxWebApp
  }
}

const raw = (): MaxWebApp | undefined => (typeof window !== 'undefined' ? window.WebApp : undefined)

/**
 * Строка initData. «МАКС» передаёт её в адресе (#WebAppData=…), мост кладёт её в sessionStorage,
 * поэтому она переживает перезагрузку страницы. Пусто — открыто вне «МАКС».
 */
export const initData = (): string => raw()?.initData ?? ''
export const isInMax = (): boolean => initData().length > 0

/**
 * Параметр запуска из кнопки бота (open_app(..., payload)): «ticket_12», «employee_5».
 * Вне «МАКС» для проверки можно открыть ?start=ticket_12.
 */
export const startParam = (): string | null =>
  raw()?.initDataUnsafe?.start_param || new URLSearchParams(location.search).get('start') || null

/**
 * Значение для заголовка X-Max-Init-Data. Заголовки HTTP принимают только Latin-1:
 * если в строке есть кириллица (например, имя в user), fetch упадёт. Тогда шлём её
 * через encodeURIComponent. Признак для бэкенда: в такой строке нет «=», её нужно один раз unquote.
 */
export const initDataHeader = (): string => {
  const v = initData()
  // eslint-disable-next-line no-control-regex
  return /^[\x00-\xFF]*$/.test(v) ? v : encodeURIComponent(v)
}

/** Мост только внутри «МАКС»: в обычном браузере скрипт есть, но транспорта нет. */
const wa = (): MaxWebApp | undefined => (isInMax() ? raw() : undefined)

/** Методы моста могут вернуть промис, который отклоняется без транспорта. */
function safe(fn: () => unknown) {
  try {
    const r = fn()
    if (r && typeof (r as Promise<unknown>).catch === 'function') (r as Promise<unknown>).catch(() => {})
  } catch {
    /* ignore */
  }
}

export function ready() {
  safe(() => wa()?.ready?.())
}

/** В «МАКС» есть системная кнопка «назад» — свою в шапке тогда не дублируем. */
export const hasNativeBack = (): boolean => !!wa()?.BackButton

let currentBack: (() => void) | null = null
/** Системная кнопка «назад» в «МАКС». null — спрятать. */
export function setBackButton(handler: (() => void) | null) {
  const bb = wa()?.BackButton
  if (!bb) return
  if (currentBack) {
    const prev = currentBack
    safe(() => bb.offClick?.(prev))
  }
  currentBack = handler
  if (handler) {
    safe(() => bb.onClick?.(handler))
    safe(() => bb.show?.())
  } else {
    safe(() => bb.hide?.())
  }
}

export const haptic = {
  tap: () => safe(() => wa()?.HapticFeedback?.impactOccurred?.('light', true)),
  select: () => safe(() => wa()?.HapticFeedback?.selectionChanged?.(true)),
  success: () => safe(() => wa()?.HapticFeedback?.notificationOccurred?.('success', true)),
  warning: () => safe(() => wa()?.HapticFeedback?.notificationOccurred?.('warning', true)),
  error: () => safe(() => wa()?.HapticFeedback?.notificationOccurred?.('error', true)),
}

/** Внешняя ссылка: в «МАКС» через мост, иначе новой вкладкой */
export function openExternal(url: string) {
  const w = wa()
  if (w?.openLink) {
    safe(() => w.openLink?.(url))
    return
  }
  window.open(url, '_blank', 'noopener')
}

export async function copyText(text: string): Promise<boolean> {
  try {
    await navigator.clipboard.writeText(text)
    return true
  } catch {
    try {
      const ta = document.createElement('textarea')
      ta.value = text
      ta.style.position = 'fixed'
      ta.style.opacity = '0'
      document.body.appendChild(ta)
      ta.select()
      const ok = document.execCommand('copy')
      ta.remove()
      return ok
    } catch {
      return false
    }
  }
}

export async function share(text: string, link: string): Promise<boolean> {
  const w = wa()
  try {
    if (w?.shareMaxContent) {
      safe(() => w.shareMaxContent?.({ text, link }))
      return true
    }
    if (navigator.share) {
      await navigator.share({ text, url: link })
      return true
    }
  } catch {
    return false
  }
  return copyText(`${text}\n${link}`)
}
