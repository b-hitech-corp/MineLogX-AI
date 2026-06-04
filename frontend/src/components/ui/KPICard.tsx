import { TrendingUp, TrendingDown, Minus } from 'lucide-react'
import { cn } from '../../utils/cn'
import type { KPIMetric } from '../../types/kpis'

const statusBar: Record<string, string> = {
  healthy: 'bg-status-healthy',
  warning: 'bg-status-warning',
  critical: 'bg-status-critical',
}

export function KPICard({ metric }: { metric: KPIMetric }) {
  const TrendIcon =
    metric.trend === 'up' ? TrendingUp : metric.trend === 'down' ? TrendingDown : Minus

  const trendColor =
    metric.trend === 'neutral'
      ? 'text-content-secondary'
      : metric.status === 'healthy'
      ? 'text-status-healthy'
      : metric.status === 'warning'
      ? 'text-status-warning'
      : 'text-status-critical'

  return (
    <div className="rounded-xl border border-surface-border bg-surface-card p-4 flex flex-col gap-3">
      <div className="flex items-start justify-between">
        <span className="text-xs font-medium text-content-secondary uppercase tracking-wide">{metric.label}</span>
        <span className={cn('flex items-center gap-1 text-xs font-medium', trendColor)}>
          <TrendIcon size={12} />
          {metric.trendValue}
        </span>
      </div>

      <div className="flex items-baseline gap-1">
        <span className="text-2xl font-bold text-content-primary">{metric.value}</span>
        {metric.unit && <span className="text-sm text-content-secondary">{metric.unit}</span>}
      </div>

      {metric.progress !== undefined && (
        <div>
          <div className="h-1.5 w-full rounded-full bg-surface-muted overflow-hidden">
            <div
              className={cn('h-full rounded-full transition-all', statusBar[metric.status])}
              style={{ width: `${Math.min(metric.progress, 100)}%` }}
            />
          </div>
          {metric.target !== undefined && (
            <p className="mt-1 text-xs text-content-tertiary">Target: {metric.target}{metric.unit}</p>
          )}
        </div>
      )}
    </div>
  )
}
