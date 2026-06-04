import { useEffect, useState } from 'react'
import { AlertTriangle } from 'lucide-react'
import { PageHeader } from '../components/layout/PageHeader'
import { FuelTable } from '../components/modules/fuel/FuelTable'
import { FuelChart } from '../components/modules/fuel/FuelChart'
import { getFuelRecords, getFuelTrend } from '../services/fuel'
import type { FuelRecord } from '../types/fuel'
import { SectionDataLoader } from '../components/ui/SectionDataLoader'
import { JsonSectionBlock } from '../components/modules/shared/JsonSectionBlock'
import { useCompanyData } from '../context/CompanyDataContext'

export function FuelPage() {
  const [records, setRecords] = useState<FuelRecord[]>([])
  const [trend, setTrend] = useState<Array<{ hour: string; consumption: number }>>([])
  const { data } = useCompanyData()

  useEffect(() => {
    getFuelRecords().then(setRecords)
    getFuelTrend().then(setTrend)
  }, [])

  const totalConsumed = records.reduce((s, r) => s + r.fuelUsedLitres, 0)
  const anomalies = records.filter((r) => r.anomaly)

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Fuel Management"
        subtitle="Shift fuel consumption and anomaly tracking"
      />

      <div className="grid grid-cols-3 gap-4">
        <div className="rounded-xl border border-surface-border bg-surface-card p-4">
          <p className="text-xs text-content-secondary mb-1">Total Consumed (Shift)</p>
          <p className="text-2xl font-bold text-content-primary">{totalConsumed.toLocaleString()} L</p>
        </div>
        <div className="rounded-xl border border-amber-800 bg-amber-900/10 p-4">
          <p className="text-xs text-content-secondary mb-1 flex items-center gap-1">
            <AlertTriangle size={11} className="text-status-warning" /> Anomalies Detected
          </p>
          <p className="text-2xl font-bold text-status-warning">{anomalies.length}</p>
        </div>
        <div className="rounded-xl border border-surface-border bg-surface-card p-4">
          <p className="text-xs text-content-secondary mb-1">Fleet Avg (L/h)</p>
          <p className="text-2xl font-bold text-content-primary">
            {records.length > 0
              ? (records.reduce((s, r) => s + r.avgConsumptionLPH, 0) / records.length).toFixed(1)
              : '—'}
          </p>
        </div>
      </div>

      <FuelChart data={trend} />
      <FuelTable records={records} />

      <SectionDataLoader>
        {data && <JsonSectionBlock section={data.fuel} title="Fuel Analytics" />}
      </SectionDataLoader>
    </div>
  )
}
