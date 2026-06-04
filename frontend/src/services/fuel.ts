import { apiFetch } from './api'
import type { FuelRecord } from '../types/fuel'
import { mockFuelRecords, mockFuelTrend } from '../mocks/fuel'

export async function getFuelRecords(): Promise<FuelRecord[]> {
  if (import.meta.env.VITE_USE_MOCK === 'true') return Promise.resolve(mockFuelRecords)
  return apiFetch<FuelRecord[]>('/fuel/records')
}

export async function getFuelTrend(): Promise<Array<{ hour: string; consumption: number }>> {
  if (import.meta.env.VITE_USE_MOCK === 'true') return Promise.resolve(mockFuelTrend)
  return apiFetch('/fuel/trend')
}
