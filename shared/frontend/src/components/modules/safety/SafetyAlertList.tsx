import { UserX, Gauge, Users, Ban, ClipboardX, AlertOctagon } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { cn } from '../../../utils/cn'
import { StatusPill } from '../../ui/StatusPill'
import { formatRelativeTime } from '../../../utils/formatters'
import type { SafetyEvent, SafetyEventType, SafetyRiskLevel } from '../../../types/safety'

const typeIcons: Record<SafetyEventType, LucideIcon> = {
  fatigue: UserX,
  speeding: Gauge,
  proximity: Users,
  'near-miss': AlertOctagon,
  ppe: ClipboardX,
  'zone-violation': Ban,
}

const typeLabels: Record<SafetyEventType, string> = {
  fatigue: 'Fatigue Risk',
  speeding: 'Speeding',
  proximity: 'Proximity Alert',
  'near-miss': 'Near Miss',
  ppe: 'PPE Violation',
  'zone-violation': 'Zone Violation',
}

const riskVariant: Record<SafetyRiskLevel, 'critical' | 'warning' | 'info' | 'inactive'> = {
  critical: 'critical',
  high: 'warning',
  medium: 'info',
  low: 'inactive',
}

export function SafetyAlertList({ events }: { events: SafetyEvent[] }) {
  return (
    <div className="columns-1 gap-3 md:columns-2 xl:columns-3">
      {events.map((event) => {
        const Icon = typeIcons[event.type]
        return (
          <div
            key={event.id}
            className={cn(
              'break-inside-avoid mb-3 rounded-xl border p-4',
              event.riskLevel === 'critical'
                ? 'border-red-800 bg-red-900/10 light:border-red-200 light:bg-red-50'
                : event.riskLevel === 'high'
                ? 'border-amber-800 bg-amber-900/10 light:border-amber-200 light:bg-amber-50'
                : 'border-surface-border bg-surface-card'
            )}
          >
            <div className="flex items-start gap-3">
              <div
                className={cn(
                  'flex h-8 w-8 items-center justify-center rounded-lg shrink-0',
                  event.riskLevel === 'critical' ? 'bg-red-900/30 light:bg-red-50' : event.riskLevel === 'high' ? 'bg-amber-900/30 light:bg-amber-50' : 'bg-surface-muted'
                )}
              >
                <Icon
                  size={15}
                  className={
                    event.riskLevel === 'critical'
                      ? 'text-red-400 light:text-red-600'
                      : event.riskLevel === 'high'
                      ? 'text-amber-400 light:text-amber-600'
                      : 'text-content-secondary'
                  }
                />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-1">
                  <span className="text-sm font-semibold text-content-primary">{typeLabels[event.type]}</span>
                  <StatusPill variant={riskVariant[event.riskLevel]} label={event.riskLevel.toUpperCase()} />
                  <StatusPill
                    variant={event.status === 'resolved' ? 'healthy' : event.status === 'investigated' ? 'info' : 'warning'}
                    label={event.status}
                    className="ml-auto"
                  />
                </div>
                <p className="text-xs text-content-secondary leading-relaxed">{event.description}</p>
                {(event.asset || event.operator) && (
                  <p className="mt-1 text-xs text-content-tertiary">
                    {[event.asset, event.operator].filter(Boolean).join(' · ')}
                  </p>
                )}
                <p className="mt-1 text-xs text-content-tertiary">{formatRelativeTime(event.timestamp)}</p>
              </div>
            </div>
          </div>
        )
      })}
    </div>
  )
}
