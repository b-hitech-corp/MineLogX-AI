import { apiFetch } from './api'
import type { GPSAsset, PitZone } from '../types/gps'
import { mockGPSAssets, mockPitZones } from '../mocks/gps'

export async function getGPSAssets(): Promise<GPSAsset[]> {
  if (import.meta.env.VITE_USE_MOCK === 'true') return Promise.resolve(mockGPSAssets)
  return apiFetch<GPSAsset[]>('/telemetry/gps')
}

export async function getPitZones(): Promise<PitZone[]> {
  if (import.meta.env.VITE_USE_MOCK === 'true') return Promise.resolve(mockPitZones)
  return apiFetch<PitZone[]>('/telemetry/zones')
}
