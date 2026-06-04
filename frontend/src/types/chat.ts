export type MessageRole = 'user' | 'assistant'

export interface InsightCardPayload {
  title: string
  metrics?: Array<{ label: string; value: string }>
  recommendation?: string
  severity?: 'info' | 'warning' | 'critical' | 'positive'
}

export interface ChatMessage {
  id: string
  role: MessageRole
  content: string
  timestamp: string
  insightCard?: InsightCardPayload
}
