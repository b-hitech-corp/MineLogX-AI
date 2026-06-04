import { useEffect, useState } from 'react'
import { PageHeader } from '../components/layout/PageHeader'
import { MaintenanceTable } from '../components/modules/maintenance/MaintenanceTable'
import { WorkOrderCard } from '../components/modules/maintenance/WorkOrderCard'
import { getMaintenanceItems, getWorkOrders } from '../services/maintenance'
import type { MaintenanceItem, WorkOrder } from '../types/maintenance'
import { SectionDataLoader } from '../components/ui/SectionDataLoader'
import { JsonSectionBlock } from '../components/modules/shared/JsonSectionBlock'
import { useCompanyData } from '../context/CompanyDataContext'

export function MaintenancePage() {
  const [items, setItems] = useState<MaintenanceItem[]>([])
  const [orders, setOrders] = useState<WorkOrder[]>([])
  const [filter, setFilter] = useState<string>('all')
  const { data } = useCompanyData()

  useEffect(() => {
    getMaintenanceItems().then(setItems)
    getWorkOrders().then(setOrders)
  }, [])

  const filtered =
    filter === 'all' ? items : items.filter((i) => i.status === filter || i.priority === filter)

  const predictive = items.filter((i) => i.predictiveFlag)
  const overdue = items.filter((i) => i.status === 'overdue')

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Asset Health & Maintenance"
        subtitle={`${items.length} maintenance items · ${predictive.length} AI predictions`}
      />

      <div className="grid grid-cols-4 gap-4">
        {[
          { label: 'AI Predictions', value: predictive.length, color: 'text-brand-blue', bg: 'bg-blue-900/20 border-blue-800' },
          { label: 'Overdue', value: overdue.length, color: 'text-status-critical', bg: 'bg-red-900/20 border-red-800' },
          { label: 'In Progress', value: items.filter((i) => i.status === 'in-progress').length, color: 'text-blue-400', bg: 'bg-blue-900/10 border-blue-900' },
          { label: 'Completed (shift)', value: items.filter((i) => i.status === 'completed').length, color: 'text-status-healthy', bg: 'bg-green-900/20 border-green-800' },
        ].map(({ label, value, color, bg }) => (
          <div key={label} className={`rounded-xl border p-4 ${bg}`}>
            <p className={`text-2xl font-bold ${color}`}>{value}</p>
            <p className="text-xs text-content-secondary mt-0.5">{label}</p>
          </div>
        ))}
      </div>

      <div className="flex items-center gap-2">
        {['all', 'critical', 'overdue', 'in-progress', 'scheduled', 'completed'].map((s) => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer capitalize ${
              filter === s
                ? 'bg-brand-blue-dim text-brand-blue'
                : 'bg-surface-card border border-surface-border text-content-secondary hover:text-content-primary'
            }`}
          >
            {s === 'all' ? 'All Items' : s}
          </button>
        ))}
      </div>

      <MaintenanceTable items={filtered} />

      {orders.length > 0 && (
        <div>
          <h2 className="text-sm font-semibold text-content-secondary uppercase tracking-wide mb-3">Work Orders</h2>
          <div className="grid grid-cols-2 gap-4">
            {orders.map((o) => (
              <WorkOrderCard key={o.id} order={o} />
            ))}
          </div>
        </div>
      )}

      <SectionDataLoader>
        {data && <JsonSectionBlock section={data.maintenance} title="Maintenance Analytics" />}
      </SectionDataLoader>
    </div>
  )
}
