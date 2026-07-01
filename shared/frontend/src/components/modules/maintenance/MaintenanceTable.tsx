import { Brain } from 'lucide-react'
import { cn } from '../../../utils/cn'
import { StatusPill } from '../../ui/StatusPill'
import type { MaintenanceItem } from '../../../types/maintenance'

const priorityVariant: Record<string, 'critical' | 'warning' | 'info' | 'inactive'> = {
  critical: 'critical',
  high: 'warning',
  medium: 'info',
  low: 'inactive',
}

const statusVariant: Record<string, 'critical' | 'warning' | 'healthy' | 'info' | 'inactive'> = {
  overdue: 'critical',
  'in-progress': 'info',
  scheduled: 'inactive',
  completed: 'healthy',
}

export function MaintenanceTable({ items }: { items: MaintenanceItem[] }) {
  return (
    <div className="rounded-xl border border-surface-border bg-surface-card overflow-hidden">
      <div className="overflow-x-auto">
        <table className="w-full text-sm">
          <thead>
            <tr className="border-b border-surface-border">
              {['Asset', 'Type', 'Status', 'Priority', 'Scheduled', 'Est. Hours', 'Assigned To', 'AI Prediction'].map(
                (h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium text-content-tertiary uppercase tracking-wide whitespace-nowrap">
                    {h}
                  </th>
                )
              )}
            </tr>
          </thead>
          <tbody>
            {items.map((item) => (
              <tr
                key={item.id}
                className={cn(
                  'border-b border-surface-border last:border-0 hover:bg-surface-muted/50 transition-colors',
                  item.predictiveFlag && 'bg-amber-900/5'
                )}
              >
                <td className="px-4 py-3 font-medium text-content-primary whitespace-nowrap">{item.assetName}</td>
                <td className="px-4 py-3 text-content-secondary">{item.type}</td>
                <td className="px-4 py-3">
                  <StatusPill variant={statusVariant[item.status]} label={item.status} />
                </td>
                <td className="px-4 py-3">
                  <StatusPill variant={priorityVariant[item.priority]} label={item.priority} />
                </td>
                <td className="px-4 py-3 text-content-secondary whitespace-nowrap">{item.scheduledDate}</td>
                <td className="px-4 py-3 text-content-secondary">{item.estimatedHours}h</td>
                <td className="px-4 py-3 text-content-secondary whitespace-nowrap">{item.assignedTo ?? '—'}</td>
                <td className="px-4 py-3 max-w-[280px]">
                  {item.predictiveFlag ? (
                    <div className="flex items-start gap-1.5">
                      <Brain size={13} className="text-brand-blue mt-0.5 shrink-0" />
                      <div>
                        <p className="text-xs text-content-secondary line-clamp-2">{item.predictiveFlag.replace('AI: ', '')}</p>
                        {item.failureProbability !== undefined && (
                          <div className="mt-1 flex items-center gap-2">
                            <div className="h-1 w-20 rounded-full bg-surface-muted overflow-hidden">
                              <div
                                className={cn(
                                  'h-full rounded-full',
                                  item.failureProbability >= 60 ? 'bg-status-critical' : 'bg-status-warning'
                                )}
                                style={{ width: `${item.failureProbability}%` }}
                              />
                            </div>
                            <span className="text-xs text-amber-400">{item.failureProbability}% risk</span>
                          </div>
                        )}
                      </div>
                    </div>
                  ) : (
                    <span className="text-xs text-content-tertiary">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
