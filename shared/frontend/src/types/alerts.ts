export type AlertSeverity = 'critical' | 'warning' | 'info'
export type AlertStatus = 'active' | 'acknowledged' | 'resolved'

export interface Alert {
  id: string
  severity: AlertSeverity
  status: AlertStatus
  title: string
  message: string
  asset?: string
  module: string
  timestamp: string
  resolvedAt?: string
}
