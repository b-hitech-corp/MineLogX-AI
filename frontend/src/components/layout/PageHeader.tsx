import type { ReactNode } from 'react'

interface PageHeaderProps {
  title: string
  subtitle?: string
  actions?: ReactNode
}

export function PageHeader({ title, subtitle, actions }: PageHeaderProps) {
  return (
    <div className="flex items-start justify-between pb-5 border-b border-glass-border">
      <div>
        <h1
          className="text-2xl font-bold uppercase tracking-tight text-content-primary leading-none"
          style={{ fontFamily: 'var(--font-display)' }}
        >
          {title}
        </h1>
        {subtitle && (
          <p
            className="mt-1.5 text-[11px] font-medium tracking-[0.1em] uppercase text-content-tertiary"
            style={{ fontFamily: 'var(--font-mono)' }}
          >
            {subtitle}
          </p>
        )}
      </div>
      {actions && <div className="flex items-center gap-2">{actions}</div>}
    </div>
  )
}
