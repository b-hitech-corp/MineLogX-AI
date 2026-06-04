import type { StatisticsSummary } from '../../../types/companyData'

interface GpsStatsBlockProps {
  statistics: Record<string, StatisticsSummary>
}

const STAT_LABELS: Record<string, string> = {
  gps_lat: 'Latitude',
  gps_lon: 'Longitude',
}

export function GpsStatsBlock({ statistics }: GpsStatsBlockProps) {
  const entries = Object.entries(statistics)
  if (entries.length === 0) return null

  return (
    <div className="flex flex-col gap-4">
      <h2 className="text-xs font-semibold text-content-secondary uppercase tracking-wide">GPS Statistics</h2>
      <div className="grid grid-cols-1 gap-4 sm:grid-cols-2">
        {entries.map(([key, stats]) => (
          <div key={key} className="rounded-xl border border-surface-border bg-surface-card p-4">
            <p className="text-xs font-semibold text-content-secondary uppercase tracking-wide mb-3">
              {STAT_LABELS[key] ?? key}
            </p>
            <div className="grid grid-cols-2 gap-2">
              {([['Mean', stats.mean], ['Std Dev', stats.std], ['Min', stats.min], ['Max', stats.max]] as [string, number][]).map(([label, val]) => (
                <div key={label}>
                  <p className="text-xs text-content-tertiary">{label}</p>
                  <p className="text-sm font-semibold text-content-primary">{val.toFixed(4)}</p>
                </div>
              ))}
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}
