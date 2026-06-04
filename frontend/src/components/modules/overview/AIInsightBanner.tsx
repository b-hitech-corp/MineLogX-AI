import { Brain, AlertTriangle, Info, CheckCircle, Zap } from 'lucide-react'
import { cn } from '../../../utils/cn'
import { formatRelativeTime } from '../../../utils/formatters'
import type { AIInsight } from '../../../types/insights'

const severityConfig = {
  critical: {
    bg: 'bg-red-900/20 border-red-800',
    text: 'text-red-400',
    icon: AlertTriangle,
  },
  warning: {
    bg: 'bg-amber-900/20 border-amber-800',
    text: 'text-amber-400',
    icon: AlertTriangle,
  },
  info: {
    bg: 'bg-blue-900/20 border-blue-800',
    text: 'text-blue-400',
    icon: Info,
  },
  positive: {
    bg: 'bg-green-900/20 border-green-800',
    text: 'text-green-400',
    icon: CheckCircle,
  },
}

interface AIInsightBannerProps {
  insights: AIInsight[]
  limit?: number
}

export function AIInsightBanner({ insights, limit = 3 }: AIInsightBannerProps) {
  const visible = insights.slice(0, limit)

  return (
    <div className="rounded-xl border border-surface-border bg-surface-card p-4">
      <div className="flex items-center gap-2 mb-3">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-blue-dim">
          <Brain size={14} className="text-brand-blue" />
        </div>
        <span className="text-sm font-semibold text-content-primary">AI Operational Insights</span>
        <span className="ml-auto flex items-center gap-1 text-xs text-content-tertiary">
          <Zap size={10} className="text-brand-blue" />
          Live
        </span>
      </div>

      <div className="flex flex-col gap-2">
        {visible.map((insight) => {
          const cfg = severityConfig[insight.severity]
          const Icon = cfg.icon
          return (
            <div key={insight.id} className={cn('flex gap-3 rounded-lg border p-3', cfg.bg)}>
              <Icon size={15} className={cn('mt-0.5 shrink-0', cfg.text)} />
              <div className="flex-1 min-w-0">
                <p className="text-sm text-content-primary leading-snug">{insight.message}</p>
                {insight.recommendation && (
                  <p className="mt-1 text-xs text-content-secondary">→ {insight.recommendation}</p>
                )}
                <p className="mt-1 text-xs text-content-tertiary">{formatRelativeTime(insight.timestamp)}</p>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
