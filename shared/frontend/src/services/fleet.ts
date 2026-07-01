import { apiFetch } from './api'
import type { FleetAsset } from '../types/fleet'
import { mockFleetAssets } from '../mocks/fleet'

export async function getFleetAssets(): Promise<FleetAsset[]> {
  if (import.meta.env.VITE_USE_MOCK === 'true') return Promise.resolve(mockFleetAssets)
  return apiFetch<FleetAsset[]>('/fleet/assets')
}
