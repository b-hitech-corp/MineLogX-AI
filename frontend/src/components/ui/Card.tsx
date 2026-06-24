import { cn } from '../../utils/cn'
import type { ReactNode } from 'react'

interface CardProps {
  children: ReactNode
  className?: string
  accent?: 'blue' | 'critical' | 'warning' | 'healthy'
}

const accentBorder: Record<string, string> = {
  blue:     'border-t-brand-blue',
  critical: 'border-t-status-critical',
  warning:  'border-t-status-warning',
  healthy:  'border-t-status-healthy',
}

export function Card({ children, className, accent }: CardProps) {
  return (
    <div
      className={cn(
        'rounded-3xl border border-glass-border bg-glass p-4 backdrop-blur-md',
        accent && `border-t-2 ${accentBorder[accent]}`,
        className
      )}
    >
      {children}
    </div>
  )
}
