import { cn } from '../../utils/cn'

type PillVariant = 'critical' | 'warning' | 'healthy' | 'inactive' | 'info'

const variants: Record<PillVariant, string> = {
  critical: 'bg-red-900/30 text-red-400 border border-red-800/50 light:bg-red-50 light:text-red-600 light:border-red-200',
  warning:  'bg-amber-900/30 text-amber-400 border border-amber-800/50 light:bg-amber-50 light:text-amber-600 light:border-amber-200',
  healthy:  'bg-green-900/30 text-green-400 border border-green-800/50 light:bg-emerald-50 light:text-emerald-600 light:border-emerald-200',
  inactive: 'bg-surface-muted text-content-secondary border border-surface-border',
  info:     'bg-blue-900/30 text-blue-400 border border-blue-800/50 light:bg-cyan-50 light:text-cyan-600 light:border-cyan-200',
}

interface StatusPillProps {
  variant: PillVariant
  label: string
  className?: string
}

export function StatusPill({ variant, label, className }: StatusPillProps) {
  return (
    <span
      className={cn(
        'inline-flex items-center rounded-full px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.08em]',
        variants[variant],
        className
      )}
      style={{ fontFamily: 'var(--font-mono)' }}
    >
      {label}
    </span>
  )
}
