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

/** Строка initData для заголовка X-Max-Init-Data. Пусто — значит, открыто вне «МАКС». */
export const initData = (): string => raw()?.initData ?? ''
export const isInMax = (): boolean => initData().length > 0

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
