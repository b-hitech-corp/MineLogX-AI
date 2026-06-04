import type { OverviewData } from '../../../types/companyData'

interface KpiSummaryBlockProps {
  overview: OverviewData
}

export function KpiSummaryBlock({ overview }: KpiSummaryBlockProps) {
  const entries = Object.entries(overview.kpi_summary.by_section)

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-xs font-semibold text-content-secondary uppercase tracking-wide">Computed KPIs by Module</h2>
      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        {entries.map(([section, count]) => (
          <div key={section} className="rounded-xl border border-surface-border bg-surface-card px-4 py-3">
            <p className="text-xs text-content-secondary mb-1 capitalize">{section.replace(/_/g, ' ')}</p>
            <p className="text-2xl font-bold text-content-primary">
              {count}
              <span className="ml-1 text-sm font-normal text-content-secondary">KPIs</span>
            </p>
          </div>
        ))}
      </div>
    </div>
  )
}
