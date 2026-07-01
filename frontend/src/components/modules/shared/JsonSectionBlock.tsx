import { ChartRenderer } from '../../ui/ChartRenderer'
import type { SectionData } from '../../../types/companyData'

interface JsonSectionBlockProps {
  section: SectionData
  title?: string
}

export function JsonSectionBlock({ section, title }: JsonSectionBlockProps) {
  const charts = section.charts ?? []
  if (charts.length === 0) return null

  return (
    <div className="flex flex-col gap-4">
      {title && (
        <h2 className="text-xs font-semibold text-content-secondary uppercase tracking-wide">{title}</h2>
      )}
      <div className="grid grid-cols-1 gap-4 lg:grid-cols-2">
        {charts.map((chart, i) => (
          <ChartRenderer
            key={`${chart.chart_type}-${i}`}
            config={chart}
            className={chart.chart_type === 'KPICards' ? 'lg:col-span-2' : ''}
          />
        ))}
      </div>
    </div>
  )
}
