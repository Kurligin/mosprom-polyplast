import { CalendarClock, Copy, Share } from 'lucide-react'
import { QRCodeSVG } from 'qrcode.react'
import { copyText, share } from '../lib/bridge'
import { Sheet, useToast } from './ui'

type Props = {
  open: boolean
  onClose: () => void
  link: string | null
  name: string
  site: string
  title?: string
  text?: string
  /** выход назначен на нерабочий день: текст от сервера (POST /api/employees → warning) */
  warning?: string | null
}

/** Лист со ссылкой и QR-кодом для оффера */
export default function InviteSheet({ open, onClose, link, name, site, title = 'Ссылка готова', text, warning }: Props) {
  const toast = useToast()
  return (
    <Sheet open={open} onClose={onClose} label="Ссылка для новичка">
      {link && (
        <>
          <h2>{title}</h2>
          <p>
            {[name, site].filter(Boolean).join(' · ')}
            <br />
            {text ?? 'Добавьте QR-код или ссылку в оффер. По ней новичок откроет Корсика в «МАКС».'}
          </p>
          {warning && (
            <div className="sheet-warning" role="status">
              <CalendarClock aria-hidden />
              <span>{warning}</span>
            </div>
          )}
          <div className="qr-box">
            <QRCodeSVG value={link} size={168} level="M" fgColor="#111827" bgColor="#FFFFFF" title={`QR-код: ${link}`} />
          </div>
          <div className="link-box">{link}</div>
          <div className="sheet-actions">
            <button
              className="btn small"
              onClick={async () => {
                const ok = await copyText(link)
                toast(ok ? 'success' : 'error', ok ? 'Ссылка скопирована' : 'Не получилось скопировать')
              }}
            >
              <Copy size={18} /> Скопировать ссылку
            </button>
            <button
              className="btn secondary small"
              onClick={async () => {
                const ok = await share('Добро пожаловать в ПОЛИПЛАСТИК! Ваш помощник в первые месяцы, Корсик:', link)
                if (!ok) toast('error', 'Не получилось поделиться. Скопируйте ссылку')
              }}
            >
              <Share size={18} /> Поделиться
            </button>
            <button className="btn plain small" onClick={onClose}>
              Готово
            </button>
          </div>
        </>
      )}
    </Sheet>
  )
}
