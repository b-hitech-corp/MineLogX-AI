export type SafetyRiskLevel = 'low' | 'medium' | 'high' | 'critical'
export type SafetyEventType = 'fatigue' | 'speeding' | 'proximity' | 'near-miss' | 'ppe' | 'zone-violation'

export interface SafetyEvent {
  id: string
  type: SafetyEventType
  riskLevel: SafetyRiskLevel
  asset?: string
  operator?: string
  description: string
  timestamp: string
  status: 'active' | 'investigated' | 'resolved'
}
