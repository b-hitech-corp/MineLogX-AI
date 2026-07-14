import { createPortal } from 'react-dom'
import { X } from 'lucide-react'
import { StatusPill } from '../ui/StatusPill'
import { useAlerts } from '../../context/AlertsContext'
import { formatRelativeTime } from '../../utils/formatters'

interface AlertsPanelProps {
  onClose: () => void
}

/** Full-height slide-in panel used for the mobile alert bell — mirrors AlertsMenu's content. */
export function AlertsPanel({ onClose }: AlertsPanelProps) {
  const { alerts, dismissAlert } = useAlerts()
  const active = alerts.filter((a) => a.status !== 'resolved')

  return createPortal(
    <>
      <div className="fixed inset-0 z-40 bg-black/40 backdrop-blur-[2px]" onClick={onClose} />

      <div className="fixed inset-y-0 right-0 z-50 flex w-full max-w-sm flex-col border-l border-surface-border bg-surface-card shadow-2xl">
        <div className="flex shrink-0 items-center justify-between px-3 pb-1 pt-2">
          <p
            className="text-[9px] font-semibold tracking-[0.15em] uppercase text-content-tertiary"
            style={{ fontFamily: 'var(--font-mono)' }}
          >
            Active Alerts
          </p>
          <button
            onClick={onClose}
            aria-label="Close alerts panel"
            className="rounded p-1 text-content-tertiary transition-colors hover:bg-surface-muted hover:text-content-secondary cursor-pointer"
          >
            <X size={14} />
          </button>
        </div>
        <div className="flex flex-1 flex-col gap-2 overflow-y-auto p-2">
          {active.map((alert) => (
            <div key={alert.id} className="flex items-start gap-3 rounded-lg border border-surface-border p-3">
              <StatusPill
                variant={alert.severity === 'critical' ? 'critical' : alert.severity === 'warning' ? 'warning' : 'info'}
                label={alert.severity.toUpperCase()}
                className="mt-0.5"
              />
              <div className="flex-1 min-w-0">
                <p className="text-sm font-medium text-content-primary truncate">{alert.title}</p>
                <p className="mt-0.5 text-xs text-content-secondary line-clamp-2">{alert.message}</p>
                <p className="mt-1 text-xs text-content-tertiary">{formatRelativeTime(alert.timestamp)}</p>
              </div>
              <button
                onClick={() => dismissAlert(alert.id)}
                className="mt-0.5 shrink-0 rounded p-1 hover:bg-surface-muted transition-colors cursor-pointer text-content-tertiary hover:text-content-secondary"
              >
                <X size={12} />
              </button>
            </div>
          ))}
          {active.length === 0 && (
            <p className="py-4 text-center text-sm text-content-tertiary">No active alerts</p>
          )}
        </div>
      </div>
    </>,
    document.body
  )
}
