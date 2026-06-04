import { useEffect, useState } from 'react'
import { Truck, Wrench, Zap, Leaf } from 'lucide-react'
import { PageHeader } from '../components/layout/PageHeader'
import { KPICategory } from '../components/modules/kpis/KPICategory'
import { AIInsightsSection } from '../components/modules/kpis/AIInsightsSection'
import { getKPIs } from '../services/kpis'
import { mockInsights } from '../mocks/insights'
import type { KPIMetric } from '../types/kpis'
import { SectionDataLoader } from '../components/ui/SectionDataLoader'
import { KpiSummaryBlock } from '../components/modules/kpis/KpiSummaryBlock'
import { useCompanyData } from '../context/CompanyDataContext'

export function KPIsPage() {
  const [kpis, setKPIs] = useState<KPIMetric[]>([])
  const { data } = useCompanyData()

  useEffect(() => {
    getKPIs().then(setKPIs)
  }, [])

  const byCategory = (cat: KPIMetric['category']) => kpis.filter((k) => k.category === cat)

  return (
    <div className="flex flex-col gap-8">
      <PageHeader
        title="KPI Dashboard"
        subtitle="Day Shift performance metrics across all operational domains"
      />

      <KPICategory
        title="Fleet Performance"
        icon={<Truck size={16} />}
        metrics={byCategory('fleet')}
      />

      <KPICategory
        title="Asset Health & Maintenance"
        icon={<Wrench size={16} />}
        metrics={byCategory('maintenance')}
      />

      <KPICategory
        title="Operational Efficiency"
        icon={<Zap size={16} />}
        metrics={byCategory('efficiency')}
      />

      <KPICategory
        title="Sustainability"
        icon={<Leaf size={16} />}
        metrics={byCategory('sustainability')}
      />

      <AIInsightsSection
        metrics={byCategory('ai')}
        insights={mockInsights}
      />

      <SectionDataLoader>
        {data && <KpiSummaryBlock overview={data.overview} />}
      </SectionDataLoader>
    </div>
  )
}
