import { cn } from '../../utils/cn'
import type { ReactNode } from 'react'

type BadgeVariant = 'default' | 'blue' | 'critical' | 'warning' | 'healthy'

const variants: Record<BadgeVariant, string> = {
  default: 'bg-surface-muted text-content-secondary',
  blue: 'bg-brand-blue-dim text-brand-blue',
  critical: 'bg-red-900/30 text-red-400 light:bg-red-50 light:text-red-600',
  warning: 'bg-amber-900/30 text-amber-400 light:bg-amber-50 light:text-amber-600',
  healthy: 'bg-green-900/30 text-green-400 light:bg-emerald-50 light:text-emerald-600',
}

interface BadgeProps {
  children: ReactNode
  variant?: BadgeVariant
  className?: string
}

export function Badge({ children, variant = 'default', className }: BadgeProps) {
  return (
    <span className={cn('inline-flex items-center rounded px-1.5 py-0.5 text-xs font-medium', variants[variant], className)}>
      {children}
    </span>
  )
}
