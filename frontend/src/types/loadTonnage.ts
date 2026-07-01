export interface HaulCycle {
  id: string
  assetId: string
  assetName: string
  route: string
  loadedAt: string
  unloadedAt?: string
  durationMin: number
  tonnage: number
  zone: string
  delayed: boolean
  delayReasonCode?: string
}

export interface TonnageShiftSummary {
  shift: string
  totalTonnes: number
  targetTonnes: number
  completedCycles: number
  avgCycleDurationMin: number
}
