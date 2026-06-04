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
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Safety & Risk Management"
        subtitle={`${events.length} events this shift · ${criticalCount} critical`}
      />

      <div className="grid grid-cols-4 gap-4">
        {[
          { label: 'Critical Events', value: events.filter((e) => e.riskLevel === 'critical').length, color: 'text-status-critical', bg: 'bg-red-900/20 border-red-800' },
          { label: 'Active Events', value: activeCount, color: 'text-status-warning', bg: 'bg-amber-900/20 border-amber-800' },
          { label: 'Investigated', value: events.filter((e) => e.status === 'investigated').length, color: 'text-blue-400', bg: 'bg-blue-900/10 border-blue-900' },
          { label: 'Resolved', value: events.filter((e) => e.status === 'resolved').length, color: 'text-status-healthy', bg: 'bg-green-900/20 border-green-800' },
        ].map(({ label, value, color, bg }) => (
          <div key={label} className={`rounded-xl border p-4 ${bg}`}>
            <p className={`text-2xl font-bold ${color}`}>{value}</p>
            <p className="text-xs text-content-secondary mt-0.5">{label}</p>
          </div>
        ))}
      </div>

      <div className="flex items-center gap-2">
        {['all', 'critical', 'high', 'active', 'investigated', 'resolved', 'fatigue', 'proximity', 'near-miss'].map((s) => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer capitalize ${
              filter === s
                ? 'bg-brand-blue-dim text-brand-blue'
                : 'bg-surface-card border border-surface-border text-content-secondary hover:text-content-primary'
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
