import { TrendingUp, TrendingDown, Minus } from 'lucide-react'
import { cn } from '../../utils/cn'
import type { KPIMetric } from '../../types/kpis'

const statusBar: Record<string, string> = {
  healthy:  'bg-status-healthy',
  warning:  'bg-status-warning',
  critical: 'bg-status-critical',
}

const statusAccent: Record<string, string> = {
  healthy:  'border-t-status-healthy',
  warning:  'border-t-status-warning',
  critical: 'border-t-status-critical',
}

export function KPICard({ metric }: { metric: KPIMetric }) {
  const TrendIcon =
    metric.trend === 'up' ? TrendingUp : metric.trend === 'down' ? TrendingDown : Minus

  const trendColor =
    metric.trend === 'neutral'
      ? 'text-content-tertiary'
      : metric.status === 'healthy'
      ? 'text-status-healthy'
      : metric.status === 'warning'
      ? 'text-status-warning'
      : 'text-status-critical'

  return (
    <div
      className={cn(
        'flex flex-col gap-3 rounded-3xl border border-t-2 border-glass-border bg-glass p-4 backdrop-blur-md transition-shadow duration-200',
        statusAccent[metric.status]
      )}
    >
      <div className="flex items-start justify-between gap-2">
        <span
          className="text-[10px] font-semibold uppercase tracking-[0.12em] text-content-tertiary leading-tight"
          style={{ fontFamily: 'var(--font-mono)' }}
        >
          {metric.label}
        </span>
        <span className={cn('flex shrink-0 items-center gap-1 text-[10px] font-semibold', trendColor)}>
          <TrendIcon size={10} />
          {metric.trendValue}
        </span>
      </div>

      <div className="flex items-baseline gap-1.5">
        <span
          className="text-3xl font-bold leading-none tabular-nums text-content-primary"
          style={{ fontFamily: 'var(--font-display)' }}
        >
          {metric.value}
        </span>
        {metric.unit && (
          <span
            className="text-xs font-medium text-content-tertiary"
            style={{ fontFamily: 'var(--font-mono)' }}
          >
            {metric.unit}
          </span>
        )}
      </div>

      {metric.progress !== undefined && (
        <div className="space-y-1">
          <div className="h-1 w-full overflow-hidden rounded-full bg-surface-muted/60">
            <div
              className={cn('h-full rounded-full transition-all duration-500', statusBar[metric.status])}
              style={{ width: `${Math.min(metric.progress, 100)}%` }}
            />
          </div>
          {metric.target !== undefined && (
            <div className="flex items-center justify-between">
              <span className="text-[9px] text-content-tertiary" style={{ fontFamily: 'var(--font-mono)' }}>
                TARGET
              </span>
              <span className="text-[9px] tabular-nums text-content-tertiary" style={{ fontFamily: 'var(--font-mono)' }}>
                {metric.target}{metric.unit}
              </span>
            </div>
          )}
        </div>
      )}
    </div>
  )
}
