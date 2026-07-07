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
    getFleetAssets().then(setAssets).catch((err) => console.error('Failed to load fleet assets:', err))
    getKPIs().then(setKPIs).catch((err) => console.error('Failed to load KPIs:', err))
  }, [])

  const summaryKPIs = kpis.filter((k) => SUMMARY_KPIS.includes(k.id))
  const activeAssets = assets.filter((a) => a.status === 'active').length
  const anomalies = assets.filter((a) => a.anomaly).length

  return (
    <div className="flex flex-col gap-4 sm:gap-6">
      <PageHeader
        title="Operational Overview"
        subtitle="Day Shift · Real-time operational intelligence"
      />

      <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-4">
        <Card className="flex items-center gap-3 col-span-1">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-green-900/30 light:bg-emerald-50">
            <Truck size={20} className="text-status-healthy" />
          </div>
          <div>
            <p className="text-2xl font-bold text-content-primary" style={{ fontFamily: 'var(--font-display)' }}>{activeAssets}</p>
            <p className="text-xs text-content-secondary">Assets Active</p>
          </div>
        </Card>
        <Card className="flex items-center gap-3 col-span-1">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-amber-900/30 light:bg-amber-50">
            <AlertTriangle size={20} className="text-status-warning" />
          </div>
          <div>
            <p className="text-2xl font-bold text-content-primary" style={{ fontFamily: 'var(--font-display)' }}>{anomalies}</p>
            <p className="text-xs text-content-secondary">Anomalies</p>
          </div>
        </Card>
        <Card className="flex items-center gap-3 col-span-1">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-blue-900/30 light:bg-cyan-50">
            <Zap size={20} className="text-brand-blue" />
          </div>
          <div>
            <p className="text-2xl font-bold text-content-primary" style={{ fontFamily: 'var(--font-display)' }}>{assets.length}</p>
            <p className="text-xs text-content-secondary">Total Fleet</p>
          </div>
        </Card>
        <Card className="flex items-center gap-3 col-span-1">
          <div className="flex h-10 w-10 items-center justify-center rounded-xl bg-red-900/30 light:bg-red-50">
            <BarChart3 size={20} className="text-status-critical" />
          </div>
          <div>
            <p className="text-2xl font-bold text-content-primary" style={{ fontFamily: 'var(--font-display)' }}>82%</p>
            <p className="text-xs text-content-secondary">vs Tonne Target</p>
          </div>
        </Card>
      </div>

      {/* Masonry body — items flow into columns and fill shortest column first */}
      <div className="space-y-4 sm:space-y-6 xl:columns-2 xl:gap-6 xl:space-y-0 2xl:columns-3">

        <div className="break-inside-avoid mb-4 sm:mb-6">
          <AIInsightBanner insights={mockInsights} limit={3} />
        </div>

        <div className="break-inside-avoid mb-4 sm:mb-6">
          <div className="columns-2 gap-3">
            {summaryKPIs.map((m) => (
              <div key={m.id} className="break-inside-avoid mb-3">
                <KPICard metric={m} />
              </div>
            ))}
          </div>
        </div>

        <div className="break-inside-avoid mb-4 sm:mb-6">
          <AlertFeed />
        </div>

        <div className="break-inside-avoid mb-4 sm:mb-6">
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

        <div className="break-inside-avoid mb-4 sm:mb-6">
          <SectionDataLoader>
            {data && <OverviewDataSummary overview={data.overview} processedAt={data.processed_at} />}
          </SectionDataLoader>
        </div>

      </div>
    </div>
  )
}
