import { useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { mockHaulCycles, mockShiftSummaries, mockTonnageTrend } from '../mocks/loadTonnage'
import { PageHeader } from '../components/layout/PageHeader'
import { LoadTonnageChart } from '../components/modules/load-tonnage/LoadTonnageChart'
import { StatusPill } from '../components/ui/StatusPill'
import { formatDuration } from '../utils/formatters'
import type { HaulCycle, TonnageShiftSummary } from '../types/loadTonnage'
import { SectionDataLoader } from '../components/ui/SectionDataLoader'
import { JsonSectionBlock } from '../components/modules/shared/JsonSectionBlock'
import { useCompanyData } from '../context/CompanyDataContext'

export function LoadTonnagePage() {
  const [cycles] = useState<HaulCycle[]>(mockHaulCycles)
  const [summaries] = useState<TonnageShiftSummary[]>(mockShiftSummaries)
  const { data } = useCompanyData()

  const current = summaries[0]
  const delayed = cycles.filter((c) => c.delayed)

  return (
    <div className="flex flex-col gap-4 sm:gap-6">
      <PageHeader
        title="Load & Tonnage"
        subtitle="Shift haul cycle performance and production tracking"
      />

      <div className="space-y-4 sm:space-y-6 xl:columns-2 xl:gap-6 xl:space-y-0">

        {current && (
          <div className="break-inside-avoid mb-4 sm:mb-6">
            <div className="grid grid-cols-2 gap-3 sm:gap-4">
              {[
                { label: 'Tonnes Moved', value: current.totalTonnes.toLocaleString() + ' t', sub: `of ${current.targetTonnes.toLocaleString()} t target`, color: 'text-status-warning' },
                { label: 'Completed Cycles', value: String(current.completedCycles), sub: 'Day shift total', color: 'text-content-primary' },
                { label: 'Avg Cycle Time', value: formatDuration(current.avgCycleDurationMin), sub: 'Target: 34 min', color: current.avgCycleDurationMin > 34 ? 'text-status-warning' : 'text-status-healthy' },
                { label: 'Delayed Cycles', value: String(delayed.length), sub: 'Route congestion', color: delayed.length > 0 ? 'text-status-critical' : 'text-status-healthy' },
              ].map(({ label, value, sub, color }) => (
                <div key={label} className="rounded-2xl border border-glass-border bg-glass backdrop-blur-md p-4">
                  <p className="text-xs text-content-secondary mb-1">{label}</p>
                  <p className={`text-2xl font-bold ${color}`} style={{ fontFamily: 'var(--font-display)' }}>{value}</p>
                  <p className="text-xs text-content-tertiary mt-0.5">{sub}</p>
                </div>
              ))}
            </div>
          </div>
        )}

        <div className="break-inside-avoid mb-4 sm:mb-6">
          <LoadTonnageChart data={mockTonnageTrend} target={current?.targetTonnes ?? 18000} />
        </div>

        <div className="break-inside-avoid mb-4 sm:mb-6">
          <div className="rounded-2xl border border-glass-border bg-glass backdrop-blur-md overflow-hidden">
            <div className="px-4 py-3 border-b border-glass-border flex items-center justify-between">
              <h3 className="text-sm font-semibold text-content-primary">Recent Haul Cycles</h3>
              {delayed.length > 0 && (
                <div className="flex items-center gap-1 text-xs text-status-warning">
                  <AlertTriangle size={12} />
                  {delayed.length} delayed
                </div>
              )}
            </div>
            <div className="overflow-x-auto">
              <table className="w-full text-sm">
                <thead>
                  <tr className="border-b border-surface-border">
                    {['Asset', 'Route', 'Duration', 'Tonnage', 'Zone', 'Status'].map((h) => (
                      <th key={h} className="px-4 py-3 text-left text-xs font-medium text-content-tertiary uppercase tracking-wide whitespace-nowrap">
                        {h}
                      </th>
                    ))}
                  </tr>
                </thead>
                <tbody>
                  {cycles.map((c) => (
                    <tr key={c.id} className="border-b border-surface-border last:border-0 hover:bg-surface-muted/50 transition-colors">
                      <td className="px-4 py-3 font-medium text-content-primary whitespace-nowrap">{c.assetName}</td>
                      <td className="px-4 py-3 text-content-secondary">{c.route}</td>
                      <td className="px-4 py-3 text-content-secondary whitespace-nowrap">{formatDuration(c.durationMin)}</td>
                      <td className="px-4 py-3 text-content-secondary">{c.tonnage}t</td>
                      <td className="px-4 py-3 text-content-secondary">{c.zone}</td>
                      <td className="px-4 py-3">
                        {c.delayed ? (
                          <StatusPill variant="warning" label="Delayed" />
                        ) : (
                          <StatusPill variant="healthy" label="On Time" />
                        )}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          </div>
        </div>

        <div className="break-inside-avoid mb-4 sm:mb-6">
          <SectionDataLoader>
            {data && <JsonSectionBlock section={data.load_and_tonnage} title="Production Analytics" />}
          </SectionDataLoader>
        </div>

      </div>
    </div>
  )
}
