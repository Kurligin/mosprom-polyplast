import { useCallback, useEffect, useState } from 'react'
import { isDemoData, loadSites } from '../api/client'
import type { Site, SiteCode } from '../api/types'

/** Короткое имя площадки для тесных мест: «Офис «Лужники»» → «Лужники», «… (GrPP)» → «GrPP», длинное без скобок → код */
export function shortSiteName(code: string, name: string | null | undefined) {
  const n = name ?? code
  const quoted = /«([^»]+)»/.exec(n)?.[1]
  if (quoted) return quoted
  const paren = /\(([^)]+)\)/.exec(n)?.[1]
  if (paren) return paren
  return n.length <= 10 ? n : code
}

const listeners = new Set<() => void>()

/** Перечитать площадки везде, где открыт справочник (после добавления новой) */
export function refreshSites() {
  loadSites(true)
    .catch(() => {})
    .finally(() => listeners.forEach((l) => l()))
}

export function useSites() {
  const [sites, setSites] = useState<Site[] | null>(null)
  const [demo, setDemo] = useState(false)
  const [tick, setTick] = useState(0)
  useEffect(() => {
    const l = () => setTick((t) => t + 1)
    listeners.add(l)
    return () => {
      listeners.delete(l)
    }
  }, [])
  useEffect(() => {
    let alive = true
    loadSites()
      .then((s) => {
        if (!alive) return
        setSites(Array.isArray(s) ? s : [])
        setDemo(isDemoData(s))
      })
      .catch(() => alive && setSites([]))
    return () => {
      alive = false
    }
  }, [tick])
  const name = useCallback((code: SiteCode | null | undefined) => {
    if (!code) return 'Все площадки'
    return sites?.find((s) => s.code === code)?.name ?? code
  }, [sites])
  return { sites: sites ?? [], loaded: sites !== null, demo, name }
}
