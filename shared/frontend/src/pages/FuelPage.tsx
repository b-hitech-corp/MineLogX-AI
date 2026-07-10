import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { PageHeader } from '../components/layout/PageHeader'
import { FuelTable } from '../components/modules/fuel/FuelTable'
import { FuelChart } from '../components/modules/fuel/FuelChart'
import { getFuelRecords, getFuelTrend } from '../services/fuel'
import type { FuelRecord } from '../types/fuel'
import { SectionDataLoader } from '../components/ui/SectionDataLoader'
import { JsonSectionBlock } from '../components/modules/shared/JsonSectionBlock'
import { SearchInput } from '../components/ui/SearchInput'
import { Pagination } from '../components/ui/Pagination'
import { usePagination } from '../hooks/usePagination'
import { useCompanyData } from '../context/CompanyDataContext'

export function FuelPage() {
  const [records, setRecords] = useState<FuelRecord[]>([])
  const [trend, setTrend] = useState<Array<{ hour: string; consumption: number }>>([])
  const [search, setSearch] = useState('')
  const { data } = useCompanyData()

  useEffect(() => {
    getFuelRecords().then(setRecords).catch((err) => console.error('Failed to load fuel records:', err))
    getFuelTrend().then(setTrend).catch((err) => console.error('Failed to load fuel trend:', err))
  }, [])

  const totalConsumed = records.reduce((s, r) => s + r.fuelUsedLitres, 0)
  const anomalies = records.filter((r) => r.anomaly)

  const filtered = records.filter((r) => r.assetName.toLowerCase().includes(search.toLowerCase()))
  const { page, setPage, totalPages, pageItems } = usePagination(filtered, 10)

  return (
    <div className="flex flex-col gap-4 sm:gap-6">
      <PageHeader
        title="Fuel Management"
        subtitle="Shift fuel consumption and anomaly tracking"
      />

      <div className="space-y-4 sm:space-y-6 xl:columns-2 xl:gap-6 xl:space-y-0">

        <div className="break-inside-avoid mb-4 sm:mb-6">
          <div className="grid grid-cols-1 gap-3 sm:grid-cols-3 sm:gap-4">
            <div className="rounded-2xl border border-glass-border bg-glass backdrop-blur-md p-4">
              <p className="text-xs text-content-secondary mb-1">Total Consumed (Shift)</p>
              <p className="text-2xl font-bold text-content-primary" style={{ fontFamily: 'var(--font-display)' }}>{totalConsumed.toLocaleString()} L</p>
            </div>
            <div className="rounded-2xl border border-amber-800 bg-amber-900/10 p-4 light:border-amber-200 light:bg-amber-50">
              <p className="text-xs text-content-secondary mb-1 flex items-center gap-1">
                <AlertTriangle size={11} className="text-status-warning" /> Anomalies Detected
              </p>
              <p className="text-2xl font-bold text-status-warning">{anomalies.length}</p>
            </div>
            <div className="rounded-2xl border border-glass-border bg-glass backdrop-blur-md p-4">
              <p className="text-xs text-content-secondary mb-1">Fleet Avg (L/h)</p>
              <p className="text-2xl font-bold text-content-primary" style={{ fontFamily: 'var(--font-display)' }}>
                {records.length > 0
                  ? (records.reduce((s, r) => s + r.avgConsumptionLPH, 0) / records.length).toFixed(1)
                  : '—'}
              </p>
            </div>
          </div>
        </div>

        <div className="break-inside-avoid mb-4 sm:mb-6">
          <FuelChart data={trend} />
        </div>

        <div className="break-inside-avoid mb-4 sm:mb-6">
          <SectionDataLoader>
            {data && <JsonSectionBlock section={data.fuel} title="Fuel Analytics" />}
          </SectionDataLoader>
        </div>

      </div>

      <div className="flex h-[65vh] min-h-[420px] flex-col gap-3">
        <SearchInput value={search} onChange={setSearch} placeholder="Search by asset..." />
        <div className="flex-1 min-h-0">
          <FuelTable records={pageItems} />
        </div>
        <Pagination page={page} totalPages={totalPages} onPageChange={setPage} />
      </div>
    </div>
  )
}
