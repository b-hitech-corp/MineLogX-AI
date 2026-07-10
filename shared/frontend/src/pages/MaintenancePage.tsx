import { useEffect, useState } from 'react'
import { PageHeader } from '../components/layout/PageHeader'
import { MaintenanceTable } from '../components/modules/maintenance/MaintenanceTable'
import { WorkOrderCard } from '../components/modules/maintenance/WorkOrderCard'
import { getMaintenanceItems, getWorkOrders } from '../services/maintenance'
import type { MaintenanceItem, WorkOrder } from '../types/maintenance'
import { SectionDataLoader } from '../components/ui/SectionDataLoader'
import { JsonSectionBlock } from '../components/modules/shared/JsonSectionBlock'
import { SearchInput } from '../components/ui/SearchInput'
import { Pagination } from '../components/ui/Pagination'
import { usePagination } from '../hooks/usePagination'
import { useCompanyData } from '../context/CompanyDataContext'

export function MaintenancePage() {
  const [items, setItems] = useState<MaintenanceItem[]>([])
  const [orders, setOrders] = useState<WorkOrder[]>([])
  const [filter, setFilter] = useState<string>('all')
  const [search, setSearch] = useState('')
  const { data } = useCompanyData()

  useEffect(() => {
    getMaintenanceItems().then(setItems).catch((err) => console.error('Failed to load maintenance items:', err))
    getWorkOrders().then(setOrders).catch((err) => console.error('Failed to load work orders:', err))
  }, [])

  const filtered = items
    .filter((i) => filter === 'all' || i.status === filter || i.priority === filter)
    .filter((i) => i.assetName.toLowerCase().includes(search.toLowerCase()))

  const { page, setPage, totalPages, pageItems } = usePagination(filtered, 10)

  const predictive = items.filter((i) => i.predictiveFlag)
  const overdue = items.filter((i) => i.status === 'overdue')

  return (
    <div className="flex flex-col gap-4 sm:gap-6">
      <PageHeader
        title="Asset Health & Maintenance"
        subtitle={`${items.length} maintenance items · ${predictive.length} AI predictions`}
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-4">
        {[
          { label: 'AI Predictions', value: predictive.length, color: 'text-brand-blue', bg: 'bg-blue-900/20 border-blue-800 light:bg-cyan-50 light:border-cyan-200' },
          { label: 'Overdue', value: overdue.length, color: 'text-status-critical', bg: 'bg-red-900/20 border-red-800 light:bg-red-50 light:border-red-200' },
          { label: 'In Progress', value: items.filter((i) => i.status === 'in-progress').length, color: 'text-blue-400 light:text-cyan-600', bg: 'bg-blue-900/10 border-blue-900 light:bg-cyan-50 light:border-cyan-200' },
          { label: 'Completed (shift)', value: items.filter((i) => i.status === 'completed').length, color: 'text-status-healthy', bg: 'bg-green-900/20 border-green-800 light:bg-emerald-50 light:border-emerald-200' },
        ].map(({ label, value, color, bg }) => (
          <div key={label} className={`rounded-2xl border p-4 backdrop-blur-sm ${bg}`}>
            <p className={`text-2xl font-bold ${color}`} style={{ fontFamily: 'var(--font-display)' }}>{value}</p>
            <p className="text-xs text-content-secondary mt-0.5">{label}</p>
          </div>
        ))}
      </div>

      <div className="flex flex-wrap items-center gap-2">
        {['all', 'critical', 'overdue', 'in-progress', 'scheduled', 'completed'].map((s) => (
          <button
            key={s}
            onClick={() => setFilter(s)}
            className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer capitalize ${
              filter === s
                ? 'bg-brand-blue-dim text-brand-blue'
                : 'bg-glass backdrop-blur-md border border-glass-border text-content-secondary hover:text-content-primary'
            }`}
          >
            {s === 'all' ? 'All Items' : s}
          </button>
        ))}
      </div>

      <div className="flex h-[65vh] min-h-[420px] flex-col gap-3">
        <SearchInput value={search} onChange={setSearch} placeholder="Search by asset..." />
        <div className="flex-1 min-h-0">
          <MaintenanceTable items={pageItems} />
        </div>
        <Pagination page={page} totalPages={totalPages} onPageChange={setPage} />
      </div>

      {orders.length > 0 && (
        <div>
          <h2 className="text-sm font-semibold text-content-secondary uppercase tracking-wide mb-3">Work Orders</h2>
          <div className="columns-1 gap-4 sm:columns-2 lg:columns-3 2xl:columns-4">
            {orders.map((o) => (
              <div key={o.id} className="break-inside-avoid mb-4">
                <WorkOrderCard order={o} />
              </div>
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
