export type TrendDirection = 'up' | 'down' | 'neutral'
export type KPIStatus = 'healthy' | 'warning' | 'critical'
export type KPICategory = 'fleet' | 'maintenance' | 'efficiency' | 'sustainability' | 'ai'

export interface KPIMetric {
  id: string
  label: string
  value: string | number
  unit?: string
  target?: number
  current?: number
  progress?: number
  trend: TrendDirection
  trendValue?: string
  status: KPIStatus
  category: KPICategory
}
