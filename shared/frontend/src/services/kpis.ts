import { apiFetch } from './api'
import type { KPIMetric } from '../types/kpis'
import { mockKPIs } from '../mocks/kpis'

export async function getKPIs(): Promise<KPIMetric[]> {
  if (import.meta.env.VITE_USE_MOCK === 'true') return Promise.resolve(mockKPIs)
  return apiFetch<KPIMetric[]>('/kpis')
}
