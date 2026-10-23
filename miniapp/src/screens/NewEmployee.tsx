import { useEffect, useState } from 'react'
import { AlertTriangle, Briefcase, Factory } from 'lucide-react'
import { api, toApiError } from '../api/client'
import type { NewEmployeeInput, NewEmployeeResult, WorkType } from '../api/types'
import InviteSheet from '../components/InviteSheet'
import { Screen, Segmented, useAsync, useToast } from '../components/ui'
import { haptic } from '../lib/bridge'
import { useNav } from '../lib/nav'
import { useSites } from '../lib/sites'

const inThreeDays = () => {
  const d = new Date()
  d.setDate(d.getDate() + 3)
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, '0')}-${String(d.getDate()).padStart(2, '0')}`
}

export default function NewEmployee() {
  const nav = useNav()
  const toast = useToast()
  const { sites, name: siteName, demo: sitesDemo, loaded: sitesLoaded } = useSites()
  const managers = useAsync(() => api.staff('manager'), [])
  const recruiters = useAsync(() => api.staff('recruiter'), [])
  const demo = sitesDemo || managers.demo || recruiters.demo

  const [form, setForm] = useState<NewEmployeeInput>({
    full_name: '',
    position: '',
    site: '',
    work_type: 'production',
    start_date: inThreeDays(),
    probation_days: 90,
    manager_id: null,
    recruiter_id: null,
  })
  const [touched, setTouched] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<NewEmployeeResult | null>(null)
  const set = <K extends keyof NewEmployeeInput>(k: K, v: NewEmployeeInput[K]) => setForm((f) => ({ ...f, [k]: v }))

  // значения по умолчанию, когда справочники загрузились
  useEffect(() => {
    if (!form.site && sites.length) set('site', sites[0].code)
  }, [sites]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (form.manager_id === null && managers.data?.length) set('manager_id', managers.data[0].id)
  }, [managers.data]) // eslint-disable-line react-hooks/exhaustive-deps
  useEffect(() => {
    if (form.recruiter_id === null && recruiters.data?.length) set('recruiter_id', recruiters.data[0].id)
  }, [recruiters.data]) // eslint-disable-line react-hooks/exhaustive-deps

  const nameOk = form.full_name.trim().split(/\s+/).filter(Boolean).length >= 2
  const nameError = touched && !nameOk ? 'Нужны фамилия и имя' : null
  const siteError = touched && !form.site ? 'Выберите площадку' : null
  const valid = nameOk && !!form.site && !!form.start_date

  const submit = async () => {
    setTouched(true)
    if (!valid) {
      haptic.warning()
      return
    }
    setBusy(true)
    try {
      const r = await api.createEmployee(
        { ...form, full_name: form.full_name.trim().replace(/\s+/g, ' '), position: form.position.trim() },
        { demo },
      )
      haptic.success()
      setResult(r)
    } catch (e) {
      toast('error', toApiError(e).message)
    } finally {
      setBusy(false)
    }
  }

  // три коротких названия влезают в сегмент; иначе обычный список
  const siteAsSegment = sites.length > 0 && sites.length <= 3 && sites.every((s) => s.name.length <= 10)

  const staffSelect = (
    label: string,
    value: number | null,
    onChange: (v: number | null) => void,
    q: typeof managers,
  ) => (
    <label className="field">
      <span className="field-label">{label}</span>
      {q.error ? (
        <span className="field-hint">
          Список не загрузился.{' '}
          <button type="button" className="link-text" onClick={q.reload}>Повторить</button>
        </span>
      ) : (
        <select
          value={value ?? ''}
          disabled={!q.data}
          onChange={(e) => onChange(e.target.value ? Number(e.target.value) : null)}
        >
          {!q.data && <option value="">Загружаем…</option>}
          {q.data && q.data.length === 0 && <option value="">Пока никого нет</option>}
          {q.data?.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
          {q.data && q.data.length > 0 && <option value="">Не назначать</option>}
        </select>
      )}
    </label>
  )

  return (
    <Screen title="Новый сотрудник" back={{ label: nav.backLabel, onClick: nav.pop }} noTabbar demo={demo} revealTitleAt={44}>
      <h1 className="large-title" style={{ padding: '4px 0 0' }}>Завести новичка</h1>
      <p className="lead">После сохранения появится ссылка и QR-код для оффера.</p>

      <section className="section">
        <div className="section-header">Сотрудник</div>
        <div className="group">
          <label className="field">
            <span className="field-label">Фамилия и имя</span>
            <input
              value={form.full_name}
              onChange={(e) => set('full_name', e.target.value)}
              onBlur={() => form.full_name && setTouched(true)}
              placeholder="Иванов Иван"
              autoComplete="off"
              autoCapitalize="words"
              enterKeyHint="next"
              aria-invalid={!!nameError}
            />
            {nameError && <span className="field-error"><AlertTriangle size={14} />{nameError}</span>}
          </label>
          <label className="field">
            <span className="field-label">Должность</span>
            <input
              value={form.position}
              onChange={(e) => set('position', e.target.value)}
              placeholder="Оператор экструдера"
              enterKeyHint="done"
            />
          </label>
        </div>
      </section>

      <section className="section">
        <div className="section-header">Где работает</div>
        <div className="group">
          <div className="field">
            <span className="field-label">Площадка</span>
            {!sitesLoaded && <div className="skeleton" style={{ height: 32, marginTop: 6, borderRadius: 9 }} />}
            {sitesLoaded && siteAsSegment && (
              <Segmented
                ariaLabel="Площадка"
                value={form.site}
                onChange={(v) => set('site', v)}
                options={sites.map((s) => ({ value: s.code, label: s.name }))}
              />
            )}
            {sitesLoaded && !siteAsSegment && (
              <select aria-label="Площадка" value={form.site} onChange={(e) => set('site', e.target.value)}>
                {sites.length === 0 && <option value="">Площадок пока нет</option>}
                {sites.map((s) => <option key={s.code} value={s.code}>{siteName(s.code)}</option>)}
              </select>
            )}
            {siteError && <span className="field-error"><AlertTriangle size={14} />{siteError}</span>}
          </div>
          <div className="field">
            <span className="field-label">Формат</span>
            <Segmented<WorkType>
              ariaLabel="Формат работы"
              value={form.work_type}
              onChange={(v) => set('work_type', v)}
              options={[
                { value: 'office', label: <><Briefcase size={15} /> Офис</> },
                { value: 'production', label: <><Factory size={15} /> Производство</> },
              ]}
            />
          </div>
        </div>
        <div className="section-footer">От формата зависят шаги: на производстве в первый день будет инструктаж по охране труда.</div>
      </section>

      <section className="section">
        <div className="section-header">Сроки</div>
        <div className="group">
          <label className="field">
            <span className="field-label">Первый рабочий день</span>
            <input type="date" value={form.start_date} onChange={(e) => set('start_date', e.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">Испытательный срок</span>
            <select value={form.probation_days} onChange={(e) => set('probation_days', Number(e.target.value))}>
              <option value={90}>3 месяца</option>
              <option value={180}>6 месяцев</option>
              <option value={60}>2 месяца</option>
            </select>
          </label>
        </div>
      </section>

      <section className="section">
        <div className="section-header">Кто ведёт</div>
        <div className="group">
          {staffSelect('Руководитель', form.manager_id, (v) => set('manager_id', v), managers)}
          {staffSelect('Рекрутер', form.recruiter_id, (v) => set('recruiter_id', v), recruiters)}
        </div>
        <div className="section-footer">Руководителю Корсик напомнит о встречах на 3‑й, 30‑й и 60‑й день.</div>
      </section>

      <div style={{ marginTop: 24 }}>
        <button className="btn" onClick={submit} disabled={busy}>
          {busy ? 'Сохраняем…' : 'Сохранить и получить ссылку'}
        </button>
      </div>

      <InviteSheet
        open={!!result}
        onClose={() => { setResult(null); nav.pop() }}
        link={result?.invite_link ?? null}
        name={result?.employee.full_name ?? ''}
        site={siteName(result?.employee.site)}
      />
    </Screen>
  )
}
