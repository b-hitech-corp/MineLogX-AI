export interface ChartSeriesConfig {
  key: string
  name: string
  color: string
  stacked?: boolean
  dot?: boolean
}

export interface ChartAxisConfig {
  key: string
  label: string
}

export interface KPICardItem {
  label: string
  value: number
  unit: string
}

export type SectionKey =
  | 'fleet'
  | 'maintenance'
  | 'load_and_tonnage'
  | 'fuel'
  | 'gps_location'
  | 'safety'
  | 'kpis'

export interface BarChartConfig {
  chart_type: 'BarChart'
  library: string
  title: string
  description: string
  data: Record<string, unknown>[]
  layout?: string
  x_axis: ChartAxisConfig
  y_axis: { label: string }
  series: ChartSeriesConfig[]
  tooltip: boolean
  legend: boolean
  grid: boolean
  section: SectionKey
}

export interface LineChartConfig {
  chart_type: 'LineChart'
  library: string
  title: string
  description: string
  data: Record<string, unknown>[]
  x_axis: ChartAxisConfig
  y_axis: { label: string }
  series: ChartSeriesConfig[]
  tooltip: boolean
  legend: boolean
  grid: boolean
  section: SectionKey
}

export interface KPICardsConfig {
  chart_type: 'KPICards'
  library: string
  title: string
  description: string
  cards: KPICardItem[]
  section: SectionKey
}

export type ChartConfig = BarChartConfig | LineChartConfig | KPICardsConfig

export interface SectionKPI {
  name: string
  value?: number
  unit?: string
  status?: string
  error?: string
}

export interface StatisticsSummary {
  count: number
  mean: number
  std: number
  min: number
  max: number
  [key: string]: unknown
}

export interface TrendData {
  date_column: string
  value_column: string
  direction: string
  r_squared: number
  slope: null | number
}

export interface SectionData {
  kpis: SectionKPI[]
  statistics: Record<string, StatisticsSummary>
  outliers: unknown[]
  trends: TrendData[]
  charts: ChartConfig[]
}

export interface OverviewFile {
  path: string
  status: string
  rows: number
  columns: number
  errors: Record<string, unknown>
}

export interface OverviewData {
  total_rows: number
  files: OverviewFile[]
  data_quality: unknown[]
  kpi_summary: {
    total_computed: number
    by_section: Record<string, number>
  }
}

export interface CompanyJSON {
  folder: string
  processed_at: string
  file_count: number
  overview: OverviewData
  fleet: SectionData
  maintenance: SectionData
  kpis: SectionData
  load_and_tonnage: SectionData
  fuel: SectionData
  gps_location: SectionData
  safety: SectionData
}
