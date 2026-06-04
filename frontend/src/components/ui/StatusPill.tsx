import { cn } from '../../utils/cn'

type PillVariant = 'critical' | 'warning' | 'healthy' | 'inactive' | 'info'

const variants: Record<PillVariant, string> = {
  critical: 'bg-red-900/40 text-red-400 border border-red-800',
  warning: 'bg-amber-900/40 text-amber-400 border border-amber-800',
  healthy: 'bg-green-900/40 text-green-400 border border-green-800',
  inactive: 'bg-surface-muted text-content-secondary',
  info: 'bg-blue-900/40 text-blue-400 border border-blue-800',
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
        'inline-flex items-center rounded-full px-2 py-0.5 text-xs font-medium',
        variants[variant],
        className
      )}
    >
      {label}
    </span>
  )
}
