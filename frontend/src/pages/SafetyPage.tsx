import { useEffect, useState } from 'react'
import { PageHeader } from '../components/layout/PageHeader'
import { SafetyAlertList } from '../components/modules/safety/SafetyAlertList'
import { mockSafetyEvents } from '../mocks/safety'
import type { SafetyEvent } from '../types/safety'
import { SectionDataLoader } from '../components/ui/SectionDataLoader'
import { JsonSectionBlock } from '../components/modules/shared/JsonSectionBlock'
import { useCompanyData } from '../context/CompanyDataContext'

export function SafetyPage() {
  const [events, setEvents] = useState<SafetyEvent[]>([])
  const { data } = useCompanyData()
  const [filter, setFilter] = useState<string>('all')

  useEffect(() => {
    setEvents(mockSafetyEvents)
  }, [])

  const filtered = filter === 'all' ? events : events.filter((e) => e.riskLevel === filter || e.type === filter || e.status === filter)

  const criticalCount = events.filter((e) => e.riskLevel === 'critical').length
  const activeCount = events.filter((e) => e.status === 'active').length

  return (
    <div className="flex flex-col gap-4 sm:gap-6">
      <PageHeader
        title="Safety & Risk Management"
        subtitle={`${events.length} events this shift · ${criticalCount} critical`}
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-4">
        {[
          { label: 'Critical Events', value: events.filter((e) => e.riskLevel === 'critical').length, color: 'text-status-critical', bg: 'bg-red-900/20 border-red-800 light:bg-red-50 light:border-red-200' },
          { label: 'Active Events', value: activeCount, color: 'text-status-warning', bg: 'bg-amber-900/20 border-amber-800 light:bg-amber-50 light:border-amber-200' },
          { label: 'Investigated', value: events.filter((e) => e.status === 'investigated').length, color: 'text-blue-400 light:text-cyan-600', bg: 'bg-blue-900/10 border-blue-900 light:bg-cyan-50 light:border-cyan-200' },
          { label: 'Resolved', value: events.filter((e) => e.status === 'resolved').length, color: 'text-status-healthy', bg: 'bg-green-900/20 border-green-800 light:bg-emerald-50 light:border-emerald-200' },
        ].map(({ label, value, color, bg }) => (
          <div key={label} className={`rounded-2xl border p-4 backdrop-blur-sm ${bg}`}>
            <p className={`text-2xl font-bold ${color}`} style={{ fontFamily: 'var(--font-display)' }}>{value}</p>
            <p className="text-xs text-content-secondary mt-0.5">{label}</p>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {['all', 'critical', 'high', 'active', 'investigated', 'resolved', 'fatigue', 'proximity', 'near-miss'].map((s) => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer capitalize ${
              filter === s
                ? 'bg-brand-blue-dim text-brand-blue'
                : 'bg-glass backdrop-blur-md border border-glass-border text-content-secondary hover:text-content-primary'
            }`}
          >
            {s === 'all' ? 'All Events' : s}
          </button>
        ))}
      </div>

      <SafetyAlertList events={filtered} />

      <SectionDataLoader>
        {data && <JsonSectionBlock section={data.safety} title="Safety Analytics" />}
      </SectionDataLoader>
    </div>
  )
}
