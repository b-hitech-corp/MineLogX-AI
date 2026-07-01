import type { ReactNode } from 'react'
import { useCompanyData } from '../../context/CompanyDataContext'

interface SectionDataLoaderProps {
  children: ReactNode
  emptyMessage?: string
}

export function SectionDataLoader({ children, emptyMessage }: SectionDataLoaderProps) {
  const { isLoading, data } = useCompanyData()

  if (isLoading) {
    return (
      <div className="flex items-center gap-2 rounded-xl border border-surface-border bg-surface-card p-4 text-content-secondary text-sm">
        <span className="h-4 w-4 animate-spin rounded-full border-2 border-surface-muted border-t-brand-blue" />
        Loading company data…
      </div>
    )
  }

  if (!data) {
    return (
      <div className="rounded-xl border border-surface-border bg-surface-card p-4 text-xs text-content-tertiary">
        {emptyMessage ?? 'No data available for this company.'}
      </div>
    )
  }

  return <>{children}</>
}
