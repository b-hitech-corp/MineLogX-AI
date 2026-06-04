import { KPICard } from '../../ui/KPICard'
import type { KPIMetric } from '../../../types/kpis'
import type { ReactNode } from 'react'

interface KPICategoryProps {
  title: string
  icon: ReactNode
  metrics: KPIMetric[]
}

export function KPICategory({ title, icon, metrics }: KPICategoryProps) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <span className="text-content-secondary">{icon}</span>
        <h3 className="text-sm font-semibold text-content-secondary uppercase tracking-wide">{title}</h3>
      </div>
      <div className="grid grid-cols-2 gap-3 lg:grid-cols-4">
        {metrics.map((m) => (
          <KPICard key={m.id} metric={m} />
        ))}
      </div>
    </div>
  )
}
