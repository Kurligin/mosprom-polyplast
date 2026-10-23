import { useState } from 'react'
import { AlertTriangle, Briefcase, Copy, Factory, Share } from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import { api } from '../api/client'
import { MANAGERS, RECRUITERS, SITES } from '../api/mocks'
import type { NewEmployeeInput, NewEmployeeResult, SiteCode, WorkType } from '../api/types'
import { Screen, Segmented, Sheet, useToast } from '../components/ui'
import { copyText, haptic, share } from '../lib/bridge'
import { useNav } from '../lib/nav'

const todayIso = () => {
  const d = new Date()
  d.setDate(d.getDate() + 3)
  return d.toISOString().slice(0, 10)
}

export default function NewEmployee() {
  const nav = useNav()
  const toast = useToast()
  const [form, setForm] = useState<NewEmployeeInput>({
    full_name: '',
    position: '',
    site: 'GTP',
    work_type: 'production',
    start_date: todayIso(),
    probation_days: 90,
    manager_id: MANAGERS[0].id,
    recruiter_id: RECRUITERS[0].id,
  })
  const [touched, setTouched] = useState(false)
  const [busy, setBusy] = useState(false)
  const [result, setResult] = useState<NewEmployeeResult | null>(null)
  const set = <K extends keyof NewEmployeeInput>(k: K, v: NewEmployeeInput[K]) => setForm((f) => ({ ...f, [k]: v }))

  const nameError = touched && form.full_name.trim().split(/\s+/).length < 2 ? 'Укажите фамилию и имя' : null
  const valid = form.full_name.trim().split(/\s+/).length >= 2 && form.position.trim() && form.start_date

  const submit = async () => {
    setTouched(true)
    if (!valid) {
      haptic.warning()
      return
    }
    setBusy(true)
    try {
      const r = await api.createEmployee({ ...form, full_name: form.full_name.trim(), position: form.position.trim() })
      haptic.success()
      setResult(r)
    } catch {
      toast('error', 'Не удалось сохранить. Попробуйте ещё раз')
    } finally {
      setBusy(false)
    }
  }

  return (
    <Screen title="Новый сотрудник" back={{ label: nav.backLabel, onClick: nav.pop }} noTabbar>
      <h1 className="large-title" style={{ padding: '4px 0 0' }}>Завести новичка</h1>
      <p className="cell-meta" style={{ margin: '4px 0 0', fontSize: 15 }}>После сохранения появится ссылка и QR-код для оффера.</p>

      <section className="section">
        <div className="section-header">Сотрудник</div>
        <div className="group">
          <label className="field">
            <span className="field-label">ФИО</span>
            <input
              value={form.full_name}
              onChange={(e) => set('full_name', e.target.value)}
              onBlur={() => setTouched(true)}
              placeholder="Иванов Иван Иванович"
              autoComplete="off"
              autoCapitalize="words"
              enterKeyHint="next"
            />
            {nameError && <span className="field-error"><AlertTriangle size={14} />{nameError}</span>}
          </label>
          <label className="field">
            <span className="field-label">Должность</span>
            <input value={form.position} onChange={(e) => set('position', e.target.value)} placeholder="Оператор экструдера" enterKeyHint="next" />
          </label>
        </div>
      </section>

      <section className="section">
        <div className="section-header">Где работает</div>
        <div className="group">
          <div className="field">
            <span className="field-label">Площадка</span>
            <Segmented<SiteCode>
              ariaLabel="Площадка"
              value={form.site}
              onChange={(v) => set('site', v)}
              options={SITES.map((s) => ({ value: s.code, label: s.name }))}
            />
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
        <div className="section-footer">От формата зависит сценарий: на производстве — инструктаж по охране труда в первый день.</div>
      </section>

      <section className="section">
        <div className="section-header">Сроки</div>
        <div className="group">
          <label className="field">
            <span className="field-label">Дата выхода</span>
            <input type="date" value={form.start_date} onChange={(e) => set('start_date', e.target.value)} />
          </label>
          <label className="field">
            <span className="field-label">Испытательный срок</span>
            <select value={form.probation_days} onChange={(e) => set('probation_days', Number(e.target.value))}>
              <option value={90}>3 месяца (90 дней)</option>
              <option value={180}>6 месяцев (180 дней)</option>
              <option value={60}>2 месяца (60 дней)</option>
            </select>
          </label>
        </div>
      </section>

      <section className="section">
        <div className="section-header">Ответственные</div>
        <div className="group">
          <label className="field">
            <span className="field-label">Руководитель</span>
            <select value={form.manager_id} onChange={(e) => set('manager_id', Number(e.target.value))}>
              {MANAGERS.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          </label>
          <label className="field">
            <span className="field-label">Рекрутер</span>
            <select value={form.recruiter_id} onChange={(e) => set('recruiter_id', Number(e.target.value))}>
              {RECRUITERS.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
            </select>
          </label>
        </div>
      </section>

      <div style={{ marginTop: 24 }}>
        <button className="btn" onClick={submit} disabled={busy}>
          {busy ? 'Сохраняем…' : 'Сохранить и получить ссылку'}
        </button>
      </div>

      <Sheet open={!!result} onClose={() => { setResult(null); nav.pop() }} label="Ссылка для новичка">
        {result && (
          <>
            <h3>Ссылка готова</h3>
            <p>
              {result.employee.full_name} · {result.employee.site}
              <br />
              Добавьте QR или ссылку в оффер — по ней новичок откроет Корсика в «МАКС».
            </p>
            <div className="qr-box">
              <QRCodeSVG value={result.invite_link} size={168} level="M" fgColor="#111827" bgColor="#FFFFFF" />
            </div>
            <div className="link-box">{result.invite_link}</div>
            <div className="sheet-actions">
              <button
                className="btn small"
                onClick={async () => {
                  const ok = await copyText(result.invite_link)
                  toast(ok ? 'success' : 'error', ok ? 'Ссылка скопирована' : 'Не удалось скопировать')
                }}
              >
                <Copy size={18} /> Скопировать ссылку
              </button>
              <button
                className="btn secondary small"
                onClick={() => share(`Добро пожаловать в ПОЛИПЛАСТИК! Ваш помощник адаптации — Корсик:`, result.invite_link)}
              >
                <Share size={18} /> Поделиться
              </button>
            </div>
            <button className="btn plain small" style={{ marginTop: 8 }} onClick={() => { setResult(null); nav.pop() }}>
              Готово
            </button>
          </>
        )}
      </Sheet>
    </Screen>
  )
}
