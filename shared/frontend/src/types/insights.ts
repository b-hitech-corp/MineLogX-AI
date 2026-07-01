export type InsightSeverity = 'info' | 'warning' | 'critical' | 'positive'

export interface AIInsight {
  id: string
  severity: InsightSeverity
  module: string
  asset?: string
  message: string
  recommendation?: string
  timestamp: string
}
