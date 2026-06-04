import { apiFetch } from './api'
import type { MaintenanceItem, WorkOrder } from '../types/maintenance'
import { mockMaintenanceItems, mockWorkOrders } from '../mocks/maintenance'

export async function getMaintenanceItems(): Promise<MaintenanceItem[]> {
  if (import.meta.env.VITE_USE_MOCK === 'true') return Promise.resolve(mockMaintenanceItems)
  return apiFetch<MaintenanceItem[]>('/maintenance/items')
}

export async function getWorkOrders(): Promise<WorkOrder[]> {
  if (import.meta.env.VITE_USE_MOCK === 'true') return Promise.resolve(mockWorkOrders)
  return apiFetch<WorkOrder[]>('/maintenance/work-orders')
}
