export type MaintenanceStatus = 'scheduled' | 'in-progress' | 'overdue' | 'completed'
export type MaintenancePriority = 'low' | 'medium' | 'high' | 'critical'

export interface MaintenanceItem {
  id: string
  assetId: string
  assetName: string
  type: string
  status: MaintenanceStatus
  priority: MaintenancePriority
  scheduledDate: string
  estimatedHours: number
  assignedTo?: string
  predictiveFlag?: string
  failureProbability?: number
  timeToFailureHours?: number
}

export interface WorkOrder {
  id: string
  maintenanceId: string
  assetId: string
  title: string
  description: string
  status: 'open' | 'in-progress' | 'completed'
  createdAt: string
}
