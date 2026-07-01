export type GPSAssetStatus = 'moving' | 'idle' | 'parked'

export interface GPSAsset {
  id: string
  assetName: string
  assetType: string
  x: number
  y: number
  zone: string
  speed: number
  heading: number
  status: GPSAssetStatus
  timestamp: string
}

export interface PitZone {
  id: string
  name: string
  type: 'pit' | 'dump' | 'workshop' | 'fuel-bay' | 'haul-road'
  x: number
  y: number
  width: number
  height: number
}
