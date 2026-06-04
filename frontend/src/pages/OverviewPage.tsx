import { useEffect, useState } from 'react'
import { Truck, Zap, AlertTriangle, BarChart3 } from 'lucide-react'
import { PageHeader } from '../components/layout/PageHeader'
import { Card } from '../components/ui/Card'
import { KPICard } from '../components/ui/KPICard'
import { SectionDataLoader } from '../components/ui/SectionDataLoader'
import { OverviewDataSummary } from '../components/modules/overview/OverviewDataSummary'
import { useCompanyData } from '../context/CompanyDataContext'
import { AIInsightBanner } from '../components/modules/overview/AIInsightBanner'
import { AlertFeed } from '../components/modules/overview/AlertFeed'
import { getFleetAssets } from '../services/fleet'
import { getKPIs } from '../services/kpis'
import { mockInsights } from '../mocks/insights'
import type { FleetAsset } from '../types/fleet'
import type { KPIMetric } from '../types/kpis'

const SUMMARY_KPIS = ['kpi-tonnes-moved', 'kpi-availability', 'kpi-fuel-per-tonne', 'kpi-utilization']

export function OverviewPage() {
  const [assets, setAssets] = useState<FleetAsset[]>([])
  const [kpis, setKPIs] = useState<KPIMetric[]>([])
  const { data } = useCompanyData()

  useEffect(() => {
    getFleetAssets().then(setAssets)
    getKPIs().then(setKPIs)
  }, [])

  const summaryKPIs = kpis.filter((k) => SUMMARY_KPIS.includes(k.id))
  const activeAssets = assets.filter((a) => a.status === 'active').length
  const anomalies = assets.filter((a) => a.anomaly).length

  return (
    <div className="flex flex-col gap-6">
      <PageHeader
        title="Operational Overview"
        subtitle="Day Shift · Real-time operational intelligence"
      />

      <AIInsightBanner insights={mockInsights} limit={3} />

      <div className="grid grid-cols-4 gap-4">
        <Card className="flex items-center gap-3 col-span-1">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-green-900/30">
            <Truck size={20} className="text-status-healthy" />
          </div>
          <div>
            <p className="text-2xl font-bold text-content-primary">{activeAssets}</p>
            <p className="text-xs text-content-secondary">Assets Active</p>
          </div>
        </Card>
        <Card className="flex items-center gap-3 col-span-1">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-900/30">
            <AlertTriangle size={20} className="text-status-warning" />
          </div>
          <div>
            <p className="text-2xl font-bold text-content-primary">{anomalies}</p>
            <p className="text-xs text-content-secondary">Anomalies</p>
          </div>
        </Card>
        <Card className="flex items-center gap-3 col-span-1">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-900/30">
            <Zap size={20} className="text-brand-blue" />
          </div>
          <div>
            <p className="text-2xl font-bold text-content-primary">{assets.length}</p>
            <p className="text-xs text-content-secondary">Total Fleet</p>
          </div>
        </Card>
        <Card className="flex items-center gap-3 col-span-1">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-red-900/30">
            <BarChart3 size={20} className="text-status-critical" />
          </div>
          <div>
            <p className="text-2xl font-bold text-content-primary">82%</p>
            <p className="text-xs text-content-secondary">vs Tonne Target</p>
          </div>
        </Card>
      </div>

      <div className="grid grid-cols-4 gap-4">
        {summaryKPIs.map((m) => (
          <KPICard key={m.id} metric={m} />
        ))}
      </div>

      <div className="grid grid-cols-3 gap-6">
        <div className="col-span-2">
          <AlertFeed />
        </div>
        <div>
          <Card>
            <h3 className="text-sm font-semibold text-content-primary mb-3">Fleet Breakdown</h3>
            {(['active', 'idle', 'maintenance', 'offline'] as const).map((status) => {
              const count = assets.filter((a) => a.status === status).length
              const pct = assets.length > 0 ? Math.round((count / assets.length) * 100) : 0
              const color =
                status === 'active' ? 'bg-status-healthy' : status === 'idle' ? 'bg-status-warning' : status === 'maintenance' ? 'bg-blue-500' : 'bg-surface-muted'
              return (
                <div key={status} className="mb-3">
                  <div className="flex justify-between mb-1">
                    <span className="text-xs capitalize text-content-secondary">{status}</span>
                    <span className="text-xs text-content-secondary">{count} · {pct}%</span>
                  </div>
                  <div className="h-1.5 w-full rounded-full bg-surface-muted overflow-hidden">
                    <div className={`h-full rounded-full ${color}`} style={{ width: `${pct}%` }} />
                  </div>
                </div>
              )
            })}
          </Card>
        </div>
      </div>

      <SectionDataLoader>
        {data && <OverviewDataSummary overview={data.overview} processedAt={data.processed_at} />}
      </SectionDataLoader>
    </div>
  )
}
