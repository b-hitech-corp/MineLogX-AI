import type { ChatMessage } from '../types/chat'

export const mockChatResponses: Record<string, ChatMessage> = {
  default: {
    id: 'resp-default',
    role: 'assistant',
    content: 'I can help you analyze operational data across fleet, maintenance, fuel, safety, and productivity. What would you like to know?',
    timestamp: new Date().toISOString(),
  },
}

export const mockInitialMessages: ChatMessage[] = [
  {
    id: 'msg-welcome',
    role: 'assistant',
    content: 'Good day. I\'m the MineLogX AI assistant. I have full visibility into your current shift operations. There are **3 active alerts** requiring attention — would you like a summary?',
    timestamp: new Date(Date.now() - 120000).toISOString(),
  },
]

export const demoResponses: Array<{ keywords: string[]; response: ChatMessage }> = [
  {
    keywords: ['alert', 'alerts', 'issue', 'issues', 'problem'],
    response: {
      id: 'resp-alerts',
      role: 'assistant',
      content: 'There are currently **3 critical/warning alerts** active this shift:',
      timestamp: new Date().toISOString(),
      insightCard: {
        title: 'Active Alert Summary',
        metrics: [
          { label: 'CAT 793-07', value: '⚠️ Engine temp anomaly — 72% failure risk' },
          { label: 'Truck 204', value: '⚠️ Fuel +18% vs 7-day avg' },
          { label: 'South Zone', value: '⚠️ Haul cycles 18% slower' },
        ],
        recommendation: 'Ground CAT 793-07 for inspection before next shift. Dispatch supervisor to Route B.',
        severity: 'warning',
      },
    },
  },
  {
    keywords: ['fuel', 'consumption', 'litres', 'liters'],
    response: {
      id: 'resp-fuel',
      role: 'assistant',
      content: 'Fuel analysis for the current shift shows one significant anomaly:',
      timestamp: new Date().toISOString(),
      insightCard: {
        title: 'Fuel Consumption Snapshot',
        metrics: [
          { label: 'Truck 204', value: '87.4 L/h (+18% vs avg)' },
          { label: 'CAT 793-07', value: '81.2 L/h (normal)' },
          { label: 'Truck 211', value: '74.1 L/h (normal)' },
          { label: 'Fleet Total', value: '71,136 L consumed' },
        ],
        recommendation: 'Inspect Truck 204 tyre pressure and fuel injectors. Abnormal drag or injection fault likely.',
        severity: 'warning',
      },
    },
  },
  {
    keywords: ['truck 204', 'tk-204'],
    response: {
      id: 'resp-truck204',
      role: 'assistant',
      content: 'Truck 204 status — active anomaly flagged this shift:',
      timestamp: new Date().toISOString(),
      insightCard: {
        title: 'Truck 204 — Asset Report',
        metrics: [
          { label: 'Status', value: 'Active — North Pit' },
          { label: 'Operator', value: 'J. Martinez' },
          { label: 'Cycles (shift)', value: '9 completed' },
          { label: 'Fuel rate', value: '87.4 L/h (+18% anomaly)' },
          { label: 'Load', value: '218t / cycle avg' },
          { label: 'Engine hours', value: '14,328 h' },
        ],
        recommendation: 'Schedule tyre inspection and fuel system check at end of current shift.',
        severity: 'warning',
      },
    },
  },
  {
    keywords: ['cat 793', 'cat-07', 'cat793', 'cat07', 'engine'],
    response: {
      id: 'resp-cat07',
      role: 'assistant',
      content: 'CAT 793-07 is flagged as **critical** — immediate action recommended:',
      timestamp: new Date().toISOString(),
      insightCard: {
        title: 'CAT 793-07 — Critical Warning',
        metrics: [
          { label: 'Failure probability', value: '72% within 24h' },
          { label: 'Anomaly duration', value: '48 hours of trending' },
          { label: 'Engine hours', value: '22,100 h' },
          { label: 'Current status', value: 'Active — East Haul Road' },
        ],
        recommendation: 'Ground immediately for emergency engine inspection. Work Order WO-9841 has been created.',
        severity: 'critical',
      },
    },
  },
  {
    keywords: ['fleet', 'trucks', 'assets', 'utilization'],
    response: {
      id: 'resp-fleet',
      role: 'assistant',
      content: 'Current fleet status across 8 assets:',
      timestamp: new Date().toISOString(),
      insightCard: {
        title: 'Fleet Status — Current Shift',
        metrics: [
          { label: 'Active', value: '5 assets' },
          { label: 'Idle', value: '1 (Truck 215 — 42 min)' },
          { label: 'Maintenance', value: '1 (Truck 208)' },
          { label: 'Offline', value: '1 (Truck 221)' },
          { label: 'Utilization', value: '68% (target: 80%)' },
        ],
        recommendation: 'Fleet utilization is 12% below target. Review Truck 215 dispatch and Truck 221 return-to-service timeline.',
        severity: 'warning',
      },
    },
  },
  {
    keywords: ['kpi', 'performance', 'tonnes', 'production'],
    response: {
      id: 'resp-kpi',
      role: 'assistant',
      content: 'Shift KPI summary as of 14:00:',
      timestamp: new Date().toISOString(),
      insightCard: {
        title: 'KPI Snapshot — Day Shift',
        metrics: [
          { label: 'Tonnes moved', value: '14,820t (82% of target)' },
          { label: 'Avg haul cycle', value: '38 min (+4 min vs target)' },
          { label: 'Fuel per tonne', value: '4.8 L/t (above 4.2 L/t target)' },
          { label: 'Availability', value: '76% (target: 85%)' },
        ],
        recommendation: 'Key drag: South Zone delays and CAT 793-07 fuel inefficiency. Addressing these would recover ~2,000t.',
        severity: 'warning',
      },
    },
  },
]
