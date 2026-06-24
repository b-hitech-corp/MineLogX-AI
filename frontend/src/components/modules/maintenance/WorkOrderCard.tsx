import { FileText } from 'lucide-react'
import { StatusPill } from '../../ui/StatusPill'
import { formatRelativeTime } from '../../../utils/formatters'
import type { WorkOrder } from '../../../types/maintenance'

export function WorkOrderCard({ order }: { order: WorkOrder }) {
  const statusMap: Record<string, 'healthy' | 'info' | 'inactive'> = {
    open: 'info',
    'in-progress': 'warning' as 'info',
    completed: 'healthy',
  }

  return (
    <div className="rounded-2xl border border-glass-border bg-glass backdrop-blur-md p-4">
      <div className="flex items-start justify-between gap-3 mb-2">
        <div className="flex items-center gap-2">
          <FileText size={14} className="text-content-secondary" />
          <span className="text-xs font-mono text-content-tertiary">{order.id}</span>
        </div>
        <StatusPill variant={statusMap[order.status]} label={order.status} />
      </div>
      <h4 className="text-sm font-semibold text-content-primary mb-1">{order.title}</h4>
      <p className="text-xs text-content-secondary leading-relaxed">{order.description}</p>
      <p className="mt-2 text-xs text-content-tertiary">Created {formatRelativeTime(order.createdAt)}</p>
    </div>
  )
}
