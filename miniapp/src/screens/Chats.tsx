import { useEffect, useRef, useState, type ReactNode } from 'react'
import { Check, Link2, Megaphone, MessagesSquare, ShieldCheck, Unplug } from 'lucide-react'
import { toApiError } from '../api/client'
import { applyPatch, audienceText, chatsApi, type ChatAudience, type TeamChat, type TeamChatPatch } from '../api/chats'
import type { SiteCode, WorkType } from '../api/types'
import { Cell, Chip, Empty, ErrorState, Screen, Section, Segmented, Sheet, SkeletonList, Switch, fmtNum, plural, useAsync, useToast } from '../components/ui'
import { haptic } from '../lib/bridge'
import { useNav } from '../lib/nav'
import { shortSiteName, useSites } from '../lib/sites'

const AUDIENCES: { value: ChatAudience; label: string }[] = [
  { value: 'none', label: 'Никому' },
  { value: 'all', label: 'Всем' },
  { value: 'sites', label: 'Площадкам' },
]

const WORK_TYPES: { value: WorkType | 'all'; label: string }[] = [
  { value: 'all', label: 'Все' },
  { value: 'office', label: 'Офис' },
  { value: 'production', label: 'Производство' },
]

const members = (n: number | null) => (n === null ? null : `${n.toLocaleString('ru-RU')} ${plural(n, ['участник', 'участника', 'участников'])}`)

/** «35 участников · добавил 5 · 2 по ссылке» */
function stats(c: TeamChat) {
  const parts = [members(c.members_count)]
  if (c.added) parts.push(`добавил ${fmtNum(c.added)} ${plural(c.added, ['новичка', 'новичков', 'новичков'])}`)
  if (c.links_sent) parts.push(`${fmtNum(c.links_sent)}\u00A0по\u00A0ссылке`)
  return parts.filter(Boolean).join(' · ')
}

function ChatIcon({ color, children }: { color: string; children: ReactNode }) {
  return <span className="cell-icon" style={{ background: color }}>{children}</span>
}

export default function Chats() {
  const nav = useNav()
  const toast = useToast()
  const { sites, name: siteName } = useSites()
  // в списке площадки короткими именами, полные — в листе настроек чата
  const shortName = (code: SiteCode) => shortSiteName(code, siteName(code))
  const { data, setData, error, reload, showSkeleton } = useAsync(() => chatsApi.list(), [])
  const [openId, setOpenId] = useState<number | null>(null)
  const [busy, setBusy] = useState(false)

  // последний список: правка приходит после await, а замыкание к этому времени устаревает
  const latest = useRef(data)
  latest.current = data

  const demo = !!data?.demo
  const chats = data?.chats ?? []
  const hr = chats.find((c) => c.is_hr_group) ?? null
  const team = chats.filter((c) => c.active && !c.is_hr_group)
  const gone = chats.filter((c) => !c.active && !c.is_hr_group)
  const open = chats.find((c) => c.id === openId) ?? null

  const put = (fn: (c: TeamChat) => TeamChat) => {
    const cur = latest.current
    if (cur) setData({ ...cur, chats: cur.chats.map(fn) })
  }

  /** Правка сразу видна на экране; сервер не принял — откатываем и объясняем */
  const save = async (chat: TeamChat, patch: TeamChatPatch, done?: string) => {
    const before = latest.current
    put((c) => (c.id === chat.id ? applyPatch(c, patch) : patch.is_hr_group ? { ...c, is_hr_group: false } : c))
    setBusy(true)
    try {
      const fresh = await chatsApi.update(chat.id, patch, { demo })
      put((c) => (c.id === fresh.id ? fresh : c))
      if (done) toast('success', done)
      if (patch.is_hr_group) reload()
    } catch (e) {
      if (before) setData(before)
      toast('error', toApiError(e).message)
    } finally {
      setBusy(false)
    }
  }

  return (
    <Screen title="Чаты команды" back={{ label: nav.backLabel, onClick: nav.pop }} demo={demo} revealTitleAt={44}>
      <h1 className="large-title" style={{ padding: '4px 0 0' }}>Чаты команды</h1>
      <p className="lead">
        В первый рабочий день Корсик пришлёт новичку ссылки для вступления в нужные чаты, а если тот согласится, представит его коллегам в чате команды.
      </p>

      {showSkeleton && <SkeletonList rows={4} />}
      {error && <ErrorState error={error} onRetry={reload} />}
      {data && chats.length === 0 && (
        <Empty
          icon={<MessagesSquare />}
          title="Чатов пока нет"
          text="Добавьте Корсика админом в чат команды или в группу HR в «МАКС», и чат появится здесь."
        />
      )}

      {data && chats.length > 0 && (
        <>
          <Section
            header="HR-группа"
            footer={
              hr
                ? 'Сюда приходят обращения и тревожные сигналы новичков. Ссылку на HR-группу бот новичкам не присылает.'
                : 'Добавьте Корсика в группу HR и нажмите там «Сделать эту группу HR-группой». Или выберите чат ниже.'
            }
          >
            {hr ? (
              <Cell
                className="top"
                left={<ChatIcon color="var(--success)"><ShieldCheck /></ChatIcon>}
                title={hr.title}
                meta={hr.active ? members(hr.members_count) : 'Бота удалили из группы: обращения никуда не приходят'}
              />
            ) : (
              <Cell
                left={<ChatIcon color="var(--warning)"><ShieldCheck /></ChatIcon>}
                title="Не выбрана"
                meta="Обращения и сигналы пока никуда не приходят"
              />
            )}
          </Section>

          <Section header="Чаты" aside={team.length || undefined} footer={team.length ? 'Нажмите на чат, чтобы выбрать, кому из новичков он нужен.' : undefined}>
            {team.length === 0 ? (
              <Cell
                left={<ChatIcon color="var(--text-secondary)"><MessagesSquare /></ChatIcon>}
                title="Других чатов нет"
                meta="Добавьте Корсика админом в чат команды"
              />
            ) : (
              team.map((c) => (
                <Cell
                  key={c.id}
                  className="top"
                  left={<ChatIcon color={c.audience === 'none' ? 'var(--text-secondary)' : 'var(--primary)'}><MessagesSquare /></ChatIcon>}
                  title={<span className="clamp-2">{c.title}</span>}
                  sub={<span className="clamp-2">{audienceText(c, shortName)}</span>}
                  meta={stats(c) || undefined}
                  chevron
                  onClick={() => setOpenId(c.id)}
                  inset={64}
                >
                  {(c.audience === 'none' || c.for_intro || !c.link) && (
                    <div className="chips">
                      {c.audience === 'none' && <Chip tone="warning">Не настроен</Chip>}
                      {c.for_intro && <Chip tone="primary" icon={<Megaphone />}>Для представлений</Chip>}
                      {!c.link && c.audience !== 'none' && <Chip>Нет ссылки</Chip>}
                    </div>
                  )}
                </Cell>
              ))
            )}
          </Section>

          {gone.length > 0 && (
            <Section header="Бота удалили" footer="Добавьте Корсика обратно, и новички снова будут попадать в эти чаты.">
              {gone.map((c) => (
                <Cell
                  key={c.id}
                  className="top"
                  left={<ChatIcon color="var(--text-secondary)"><Unplug /></ChatIcon>}
                  title={<span className="clamp-2">{c.title}</span>}
                  meta={<span className="clamp-2">{audienceText(c, shortName)}</span>}
                  inset={64}
                />
              ))}
            </Section>
          )}
        </>
      )}

      <ChatSheet
        chat={open}
        sites={sites.map((s) => s.code)}
        siteName={siteName}
        busy={busy}
        onSave={save}
        onClose={() => setOpenId(null)}
      />
    </Screen>
  )
}

type SheetProps = {
  chat: TeamChat | null
  sites: SiteCode[]
  siteName: (code: SiteCode) => string
  busy: boolean
  onSave: (chat: TeamChat, patch: TeamChatPatch, done?: string) => Promise<void>
  onClose: () => void
}

/** Настройки одного чата. Каждое изменение сохраняется сразу, как в настройках телефона */
function ChatSheet({ chat, sites, siteName, busy, onSave, onClose }: SheetProps) {
  const [confirmHr, setConfirmHr] = useState(false)
  useEffect(() => setConfirmHr(false), [chat?.id])

  const toggleSite = (c: TeamChat, code: SiteCode) => {
    haptic.select()
    const next = c.sites.includes(code) ? c.sites.filter((s) => s !== code) : [...c.sites, code]
    onSave(c, { sites: next })
  }

  // площадки, которых уже нет в справочнике, тоже показываем: их можно снять
  const siteCodes = chat ? [...new Set([...sites, ...chat.sites])] : sites

  return (
    <Sheet open={!!chat} onClose={onClose} label="Настройки чата">
      {chat && (
        <>
          <h2>{chat.title}</h2>
          <p>{stats(chat) || 'Корсик в чате'}</p>

          <section className="section">
            <h3 className="section-header">Кому нужен чат</h3>
            <div className="group">
              <div className="field">
                <Segmented
                  ariaLabel="Кому нужен чат"
                  value={chat.audience}
                  onChange={(v) => v !== chat.audience && onSave(chat, { audience: v })}
                  options={AUDIENCES}
                  tall
                />
              </div>
            </div>
            <div className="section-footer">
              {chat.audience === 'none'
                ? 'Новичкам ссылку сюда не присылаем. Подходит для чатов, где новичкам пока рано.'
                : chat.audience === 'all'
                  ? 'Новичку любой площадки бот пришлёт ссылку сюда в его первый рабочий день.'
                  : chat.sites.length
                    ? `Новичкам ${chat.sites.length === 1 ? 'площадки' : 'площадок'} ${chat.sites.map((c) => shortSiteName(c, siteName(c))).join(', ')} бот пришлёт ссылку сюда в первый рабочий день.`
                    : 'Выберите площадки ниже.'}
            </div>
          </section>

          {chat.audience === 'sites' && (
            <section className="section">
              <h3 className="section-header">Площадки</h3>
              <div className="group">
                {siteCodes.map((code) => (
                  <Cell
                    key={code}
                    left={<span className={`check${chat.sites.includes(code) ? ' on' : ''}`}><Check /></span>}
                    title={siteName(code)}
                    inset={52}
                    onClick={() => toggleSite(chat, code)}
                    disabled={busy}
                  />
                ))}
              </div>
            </section>
          )}

          {chat.audience !== 'none' && (
            <section className="section">
              <h3 className="section-header">Кто из новичков</h3>
              <div className="group">
                <div className="field">
                  <Segmented
                    ariaLabel="Офис или производство"
                    value={chat.work_type ?? 'all'}
                    onChange={(v) => v !== (chat.work_type ?? 'all') && onSave(chat, { work_type: v === 'all' ? null : v })}
                    options={WORK_TYPES}
                    tall
                  />
                </div>
              </div>
            </section>
          )}

          <section className="section">
            <div className="group">
              <Cell
                left={<ChatIcon color="var(--primary)"><Megaphone /></ChatIcon>}
                title="Для представлений"
                value={
                  <Switch
                    checked={chat.for_intro}
                    onChange={(v) => onSave(chat, { for_intro: v })}
                    label="Чат для представлений"
                    disabled={busy}
                  />
                }
              />
            </div>
            <div className="section-footer">
              Сюда бот публикует рассказ новичка о себе, если тот согласится. Без отметки рассказ уходит в чат площадки новичка.
            </div>
          </section>

          <section className="section">
            <div className="group">
              <Cell
                left={<ChatIcon color={chat.link ? 'var(--success)' : 'var(--warning)'}><Link2 /></ChatIcon>}
                title={chat.link ? 'Ссылка-приглашение есть' : 'Ссылки-приглашения нет'}
                meta={
                  chat.link
                    ? 'Корсик пришлёт её новичку в первый рабочий день'
                    : 'Сделайте Корсика админом чата: так он увидит ссылку и сможет публиковать рассказы новичков'
                }
              />
            </div>
          </section>

          <div className="sheet-actions">
            {confirmHr ? (
              <button
                className="btn small"
                disabled={busy}
                onClick={async () => {
                  await onSave(chat, { is_hr_group: true }, 'Теперь это HR-группа')
                  onClose()
                }}
              >
                <ShieldCheck size={18} /> Да, присылать обращения сюда
              </button>
            ) : (
              <button className="btn secondary small" disabled={busy} onClick={() => { haptic.tap(); setConfirmHr(true) }}>
                <ShieldCheck size={18} /> Сделать HR-группой
              </button>
            )}
            <button className="btn plain small" onClick={onClose}>Готово</button>
          </div>
        </>
      )}
    </Sheet>
  )
}
