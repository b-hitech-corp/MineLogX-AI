export interface FuelRecord {
  id: string
  assetId: string
  assetName: string
  location: string
  fuelUsedLitres: number
  fuelEfficiencyLPT: number
  avgConsumptionLPH: number
  sevenDayAvgLPH: number
  anomaly: boolean
  anomalyPercent?: number
  timestamp: string
}
