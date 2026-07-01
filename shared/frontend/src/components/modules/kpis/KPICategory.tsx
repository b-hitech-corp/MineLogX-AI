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
      <div className="columns-2 gap-3 lg:columns-4">
        {metrics.map((m) => (
          <div key={m.id} className="break-inside-avoid mb-3">
            <KPICard metric={m} />
          </div>
        ))}
      </div>
    </div>
  )
}
