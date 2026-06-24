import type { OverviewData } from '../../../types/companyData'

interface OverviewDataSummaryProps {
  overview: OverviewData
  processedAt: string
}

export function OverviewDataSummary({ overview, processedAt }: OverviewDataSummaryProps) {
  const sectionEntries = Object.entries(overview.kpi_summary.by_section).filter(([, v]) => v > 0)

  return (
    <div className="flex flex-col gap-4">
      <div className="flex items-center justify-between">
        <h2 className="text-xs font-semibold text-content-secondary uppercase tracking-wide">Data Summary</h2>
        <span className="text-xs text-content-tertiary">
          Processed {new Date(processedAt).toLocaleString()}
        </span>
      </div>

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-3">
        <div className="rounded-xl border border-surface-border bg-surface-card px-4 py-3">
          <p className="text-xs text-content-secondary mb-1">Total Files</p>
          <p className="text-2xl font-bold text-content-primary">{overview.files.length}</p>
        </div>
        <div className="rounded-xl border border-surface-border bg-surface-card px-4 py-3">
          <p className="text-xs text-content-secondary mb-1">Total Rows</p>
          <p className="text-2xl font-bold text-content-primary">{overview.total_rows.toLocaleString()}</p>
        </div>
        <div className="rounded-xl border border-surface-border bg-surface-card px-4 py-3">
          <p className="text-xs text-content-secondary mb-1">KPIs Computed</p>
          <p className="text-2xl font-bold text-content-primary">{overview.kpi_summary.total_computed}</p>
        </div>
      </div>

      <div className="rounded-xl border border-surface-border bg-surface-card p-4">
        <p className="text-xs font-semibold text-content-secondary uppercase tracking-wide mb-3">Files Processed</p>
        <div className="flex flex-col gap-2">
          {overview.files.map((file) => (
            <div key={file.path} className="flex items-center justify-between text-xs">
              <span className="text-content-secondary font-mono truncate max-w-[60%]">{file.path}</span>
              <div className="flex items-center gap-3">
                <span className="text-content-tertiary">{file.rows.toLocaleString()} rows</span>
                <span className={`rounded-full px-2 py-0.5 font-medium ${
                  file.status === 'success'
                    ? 'bg-green-900/40 text-green-400 border border-green-800 light:bg-emerald-50 light:text-emerald-600 light:border-emerald-200'
                    : 'bg-red-900/40 text-red-400 border border-red-800 light:bg-red-50 light:text-red-600 light:border-red-200'
                }`}>
                  {file.status}
                </span>
              </div>
            </div>
          ))}
        </div>
      </div>

      {sectionEntries.length > 0 && (
        <div className="rounded-xl border border-surface-border bg-surface-card p-4">
          <p className="text-xs font-semibold text-content-secondary uppercase tracking-wide mb-3">KPIs by Section</p>
          <div className="grid grid-cols-2 gap-2 sm:grid-cols-3">
            {sectionEntries.map(([section, count]) => (
              <div key={section} className="flex items-center justify-between rounded-lg border border-surface-border px-3 py-2">
                <span className="text-xs text-content-secondary capitalize">{section.replace(/_/g, ' ')}</span>
                <span className="text-xs font-bold text-brand-blue">{count}</span>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  )
}
