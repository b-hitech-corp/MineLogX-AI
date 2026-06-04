import { Bar, Line } from 'react-chartjs-2'
import {
  Chart,
  CategoryScale,
  LinearScale,
  BarElement,
  LineElement,
  PointElement,
  Tooltip,
  Legend,
  Filler,
} from 'chart.js'
import type { ChartConfig, KPICardItem } from '../../types/companyData'

Chart.register(CategoryScale, LinearScale, BarElement, LineElement, PointElement, Tooltip, Legend, Filler)

const GRID_COLOR = '#21262D'
const TICK_COLOR = '#484F58'
const LEGEND_COLOR = '#8B949E'

const darkScales = {
  x: {
    grid: { color: GRID_COLOR },
    ticks: { color: TICK_COLOR, font: { size: 11 } },
    border: { display: false },
  },
  y: {
    grid: { color: GRID_COLOR },
    ticks: { color: TICK_COLOR, font: { size: 11 } },
    border: { display: false },
  },
}

const darkTooltip = {
  backgroundColor: '#161B22',
  borderColor: GRID_COLOR,
  borderWidth: 1,
  titleColor: LEGEND_COLOR,
  bodyColor: '#F0F6FC',
}

interface ChartRendererProps {
  config: ChartConfig
  className?: string
}

function KpiCards({ cards }: { cards: KPICardItem[] }) {
  return (
    <div className="flex flex-wrap gap-3">
      {cards.map((card) => (
        <div
          key={card.label}
          className="flex-1 min-w-[140px] rounded-xl border border-surface-border bg-surface-card px-4 py-3"
        >
          <p className="text-xs text-content-secondary mb-1">{card.label}</p>
          <p className="text-2xl font-bold text-content-primary">
            {card.value.toLocaleString(undefined, { maximumFractionDigits: 2 })}
            <span className="ml-1 text-sm font-normal text-content-secondary">{card.unit}</span>
          </p>
        </div>
      ))}
    </div>
  )
}

export function ChartRenderer({ config, className = '' }: ChartRendererProps) {
  const cardBase = 'rounded-xl border border-surface-border bg-surface-card p-4'

  if (config.chart_type === 'KPICards') {
    return (
      <div className={`${cardBase} ${className}`}>
        <p className="text-xs font-semibold text-content-secondary uppercase tracking-wide mb-3">{config.title}</p>
        <KpiCards cards={config.cards} />
      </div>
    )
  }

  const labels = config.data.map((row) => String(row[config.x_axis.key] ?? ''))
  const datasets = config.series.map((s) => ({
    label: s.name,
    data: config.data.map((row) => Number(row[s.key] ?? 0)),
    backgroundColor: s.color + '99',
    borderColor: s.color,
    borderWidth: 2,
    pointRadius: s.dot === false ? 0 : 3,
    fill: false,
  }))

  const baseOptions = {
    responsive: true,
    maintainAspectRatio: false,
    plugins: {
      legend: {
        display: config.legend,
        labels: { color: LEGEND_COLOR, font: { size: 11 }, boxWidth: 12 },
      },
      tooltip: darkTooltip,
    },
    scales: darkScales,
  }

  if (config.chart_type === 'BarChart') {
    return (
      <div className={`${cardBase} ${className}`}>
        <p className="text-xs font-semibold text-content-secondary uppercase tracking-wide mb-1">{config.title}</p>
        <p className="text-xs text-content-tertiary mb-3">{config.description}</p>
        <div className="h-56">
          <Bar data={{ labels, datasets }} options={baseOptions} />
        </div>
      </div>
    )
  }

  // LineChart
  return (
    <div className={`${cardBase} ${className}`}>
      <p className="text-xs font-semibold text-content-secondary uppercase tracking-wide mb-1">{config.title}</p>
      <p className="text-xs text-content-tertiary mb-3">{config.description}</p>
      <div className="h-56">
        <Line data={{ labels, datasets }} options={baseOptions} />
      </div>
    </div>
  )
}
