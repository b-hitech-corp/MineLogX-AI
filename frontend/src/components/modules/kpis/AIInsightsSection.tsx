import { Brain } from 'lucide-react'
import { KPICard } from '../../ui/KPICard'
import { AIInsightBanner } from '../overview/AIInsightBanner'
import type { KPIMetric } from '../../../types/kpis'
import type { AIInsight } from '../../../types/insights'

interface AIInsightsSectionProps {
  metrics: KPIMetric[]
  insights: AIInsight[]
}

export function AIInsightsSection({ metrics, insights }: AIInsightsSectionProps) {
  return (
    <div>
      <div className="flex items-center gap-2 mb-3">
        <Brain size={16} className="text-brand-blue" />
        <h3 className="text-sm font-semibold text-content-secondary uppercase tracking-wide">AI Insights & Recommendations</h3>
      </div>
      <div className="grid grid-cols-3 gap-3 mb-4">
        {metrics.map((m) => (
          <KPICard key={m.id} metric={m} />
        ))}
      </div>
      <AIInsightBanner insights={insights} limit={4} />
    </div>
  )
}
