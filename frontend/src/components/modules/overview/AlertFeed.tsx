import { X } from 'lucide-react'
import { StatusPill } from '../../ui/StatusPill'
import { useAlerts } from '../../../context/AlertsContext'
import { formatRelativeTime } from '../../../utils/formatters'

export function AlertFeed() {
  const { alerts, dismissAlert } = useAlerts()
  const active = alerts.filter((a) => a.status !== 'resolved').slice(0, 6)

  return (
    <div className="rounded-xl border border-surface-border bg-surface-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-content-primary">Active Alerts</h3>
      <div className="flex flex-col gap-2">
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
  )
}
