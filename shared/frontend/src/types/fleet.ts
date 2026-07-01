export type FleetStatus = 'active' | 'idle' | 'maintenance' | 'offline'
export type AssetType = 'haul-truck' | 'excavator' | 'loader' | 'dozer'

export interface FleetAsset {
  id: string
  name: string
  type: AssetType
  status: FleetStatus
  operator?: string
  location: string
  engineHours: number
  fuelLevel: number
  speedKph: number
  loadTonnes: number
  cyclesCompleted: number
  fuelConsumptionLPH: number
  aiAlert?: string
  anomaly?: boolean
}
