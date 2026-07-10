import { AlertTriangle } from 'lucide-react'
import { cn } from '../../../utils/cn'
import type { FuelRecord } from '../../../types/fuel'

export function FuelTable({ records }: { records: FuelRecord[] }) {
  return (
    <div className="flex h-full flex-col rounded-xl border border-surface-border bg-surface-card overflow-hidden">
      <div className="flex-1 overflow-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 z-10 bg-surface-card">
            <tr className="border-b border-surface-border">
              {['Asset', 'Location', 'Fuel Used (L)', 'L/h (Current)', '7-Day Avg L/h', 'L/t', 'Status'].map(
                (h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium text-content-tertiary uppercase tracking-wide whitespace-nowrap">
                    {h}
                  </th>
                )
              )}
            </tr>
          </thead>
          <tbody>
            {records.map((r) => (
              <tr
                key={r.id}
                className={cn(
                  'border-b border-surface-border last:border-0 hover:bg-surface-muted/50 transition-colors',
                  r.anomaly && 'bg-amber-900/5'
                )}
              >
                <td className="px-4 py-3 font-medium text-content-primary whitespace-nowrap">{r.assetName}</td>
                <td className="px-4 py-3 text-content-secondary">{r.location}</td>
                <td className="px-4 py-3 text-content-secondary">{r.fuelUsedLitres.toLocaleString()}</td>
                <td className="px-4 py-3">
                  <span className={cn('font-medium', r.anomaly ? 'text-status-warning' : 'text-content-secondary')}>
                    {r.avgConsumptionLPH.toFixed(1)}
                  </span>
                </td>
                <td className="px-4 py-3 text-content-secondary">{r.sevenDayAvgLPH.toFixed(1)}</td>
                <td className="px-4 py-3 text-content-secondary">{r.fuelEfficiencyLPT > 0 ? r.fuelEfficiencyLPT.toFixed(1) : '—'}</td>
                <td className="px-4 py-3">
                  {r.anomaly ? (
                    <div className="flex items-center gap-1.5 text-amber-400">
                      <AlertTriangle size={13} />
                      <span className="text-xs">+{r.anomalyPercent}% anomaly</span>
                    </div>
                  ) : (
                    <span className="text-xs text-status-healthy">Normal</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
