import { cn } from '../../utils/cn'
import type { ReactNode } from 'react'

interface CardProps {
  children: ReactNode
  className?: string
}

export function Card({ children, className }: CardProps) {
  return (
    <div className={cn('rounded-xl border border-surface-border bg-surface-card p-4', className)}>
      {children}
    </div>
  )
}
