import type { AIInsight } from '../types/insights'

export const mockInsights: AIInsight[] = [
  {
    id: 'INS-001',
    severity: 'critical',
    module: 'maintenance',
    asset: 'CAT 793-07',
    message: 'CAT 793-07 engine temperature trending upward for 48 hrs — 72% failure probability within 24 hrs.',
    recommendation: 'Ground vehicle for emergency inspection before next shift. Assign Workshop Team A.',
    timestamp: '2026-06-03T13:45:00Z',
  },
  {
    id: 'INS-002',
    severity: 'warning',
    module: 'fuel',
    asset: 'Truck 204',
    message: 'Fuel consumption on Truck 204 increased 18% versus the 7-day average.',
    recommendation: 'Inspect tyre pressure, fuel injectors, and haul route conditions.',
    timestamp: '2026-06-03T11:20:00Z',
  },
  {
    id: 'INS-003',
    severity: 'warning',
    module: 'fleet',
    message: 'South Zone haul cycles 18% slower than shift baseline — possible route congestion.',
    recommendation: 'Dispatch supervisor to assess Route B surface conditions. Consider alternate routing.',
    timestamp: '2026-06-03T10:05:00Z',
  },
  {
    id: 'INS-004',
    severity: 'warning',
    module: 'maintenance',
    asset: 'Excavator EX-12',
    message: 'Predicted maintenance window for Asset EX-12 within 36 operating hours.',
    recommendation: 'Schedule hydraulic system service for 2026-06-06 before window expires.',
    timestamp: '2026-06-03T08:15:00Z',
  },
  {
    id: 'INS-005',
    severity: 'info',
    module: 'fleet',
    message: 'Fleet utilization below target during current shift — 68% vs 80% target.',
    recommendation: 'Two assets are idle beyond threshold. Review dispatch schedule.',
    timestamp: '2026-06-03T12:00:00Z',
  },
  {
    id: 'INS-006',
    severity: 'positive',
    module: 'fleet',
    message: 'Haul Route A cycle time improved 9% after operational changes implemented yesterday.',
    timestamp: '2026-06-03T07:30:00Z',
  },
]
