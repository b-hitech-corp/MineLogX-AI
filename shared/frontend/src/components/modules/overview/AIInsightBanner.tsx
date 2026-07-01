import { Brain, AlertTriangle, Info, CheckCircle, Zap } from 'lucide-react'
import { cn } from '../../../utils/cn'
import { formatRelativeTime } from '../../../utils/formatters'
import type { AIInsight } from '../../../types/insights'

const severityConfig = {
  critical: {
    bg: 'bg-red-900/15 border-red-800/40 light:bg-red-50 light:border-red-200',
    text: 'text-red-400 light:text-red-600',
    label: 'bg-red-900/30 text-red-400 light:bg-red-100 light:text-red-600',
    icon: AlertTriangle,
  },
  warning: {
    bg: 'bg-amber-900/15 border-amber-800/40 light:bg-amber-50 light:border-amber-200',
    text: 'text-amber-400 light:text-amber-600',
    label: 'bg-amber-900/30 text-amber-400 light:bg-amber-100 light:text-amber-600',
    icon: AlertTriangle,
  },
  info: {
    bg: 'bg-blue-900/15 border-blue-800/40 light:bg-cyan-50 light:border-cyan-200',
    text: 'text-blue-400 light:text-cyan-600',
    label: 'bg-blue-900/30 text-blue-400 light:bg-cyan-100 light:text-cyan-600',
    icon: Info,
  },
  positive: {
    bg: 'bg-green-900/15 border-green-800/40 light:bg-emerald-50 light:border-emerald-200',
    text: 'text-green-400 light:text-emerald-600',
    label: 'bg-green-900/30 text-green-400 light:bg-emerald-100 light:text-emerald-600',
    icon: CheckCircle,
  },
}

const severityLabel: Record<string, string> = {
  critical: 'CRITICAL',
  warning: 'WARNING',
  info: 'INFO',
  positive: 'RESOLVED',
}

interface AIInsightBannerProps {
  insights: AIInsight[]
  limit?: number
}

export function AIInsightBanner({ insights, limit = 3 }: AIInsightBannerProps) {
  const visible = insights.slice(0, limit)

  return (
    <div className="rounded-3xl border border-glass-border bg-glass backdrop-blur-md overflow-hidden">
      {/* Header */}
      <div className="flex items-center gap-3 border-b border-glass-border px-4 py-3">
        <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-blue-dim">
          <Brain size={13} className="text-brand-blue" />
        </div>
        <div>
          <span
            className="text-[10px] font-semibold tracking-[0.14em] uppercase text-content-tertiary block"
            style={{ fontFamily: 'var(--font-mono)' }}
          >
            AI Engine
          </span>
          <span className="text-sm font-semibold text-content-primary leading-none">
            Operational Insights
          </span>
        </div>
        <div className="ml-auto flex items-center gap-1.5">
          <span className="relative flex h-1.5 w-1.5">
            <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-brand-blue opacity-60" />
            <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-brand-blue" />
          </span>
          <span
            className="text-[10px] font-medium tracking-wide text-brand-blue"
            style={{ fontFamily: 'var(--font-mono)' }}
          >
            <Zap size={9} className="inline mr-0.5" />
            Live
          </span>
        </div>
      </div>

      {/* Insights */}
      <div className="flex flex-col divide-y divide-glass-border">
        {visible.map((insight) => {
          const cfg = severityConfig[insight.severity]
          const Icon = cfg.icon
          return (
            <div key={insight.id} className="flex gap-3 px-4 py-3">
              <div className={cn('mt-0.5 shrink-0 rounded p-1', cfg.label)}>
                <Icon size={11} />
              </div>
              <div className="flex-1 min-w-0">
                <div className="flex items-center gap-2 mb-0.5">
                  <span
                    className={cn('text-[9px] font-bold tracking-[0.14em]', cfg.text)}
                    style={{ fontFamily: 'var(--font-mono)' }}
                  >
                    {severityLabel[insight.severity]}
                  </span>
                  {insight.asset && (
                    <span
                      className="text-[9px] font-medium text-content-tertiary tracking-wide"
                      style={{ fontFamily: 'var(--font-mono)' }}
                    >
                      · {insight.asset}
                    </span>
                  )}
                </div>
                <p className="text-sm text-content-primary leading-snug">{insight.message}</p>
                {insight.recommendation && (
                  <p className="mt-1 text-xs text-content-secondary leading-snug">
                    <span className="text-brand-blue font-medium">→</span> {insight.recommendation}
                  </p>
                )}
                <p
                  className="mt-1 text-[10px] text-content-tertiary"
                  style={{ fontFamily: 'var(--font-mono)' }}
                >
                  {formatRelativeTime(insight.timestamp)}
                </p>
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
