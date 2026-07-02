import { Brain } from 'lucide-react'
import { cn } from '../../../utils/cn'
import type { ChatMessage as ChatMessageType } from '../../../types/chat'
import { formatRelativeTime } from '../../../utils/formatters'

const severityBg: Record<string, string> = {
  critical: 'border-red-800 bg-red-900/20 light:border-red-200 light:bg-red-50',
  warning: 'border-amber-800 bg-amber-900/20 light:border-amber-200 light:bg-amber-50',
  positive: 'border-green-800 bg-green-900/20 light:border-emerald-200 light:bg-emerald-50',
  info: 'border-blue-800 bg-blue-900/20 light:border-cyan-200 light:bg-cyan-50',
}

export function ChatMessage({ message }: { message: ChatMessageType }) {
  const isUser = message.role === 'user'

  return (
    <div className={cn('flex gap-2', isUser && 'flex-row-reverse')}>
      <div
        className={cn(
          'flex h-7 w-7 shrink-0 items-center justify-center rounded-lg text-xs font-bold',
          isUser ? 'bg-brand-blue-dim text-brand-blue' : 'bg-surface-muted text-content-secondary'
        )}
      >
        {isUser ? 'U' : <Brain size={13} />}
      </div>

      <div className={cn('flex max-w-[320px] flex-col gap-1', isUser && 'items-end')}>
        <div
          className={cn(
            'rounded-xl px-3 py-2.5 text-sm leading-relaxed',
            isUser
              ? 'bg-brand-blue-dim text-content-primary rounded-tr-sm'
              : 'bg-surface text-content-primary rounded-tl-sm border border-surface-border'
          )}
          dangerouslySetInnerHTML={{
            __html: message.content
              .replace(/\*\*(.*?)\*\*/g, '<strong>$1</strong>')
              .replace(/\n/g, '<br>'),
          }}
        />

        {message.insightCard && (
          <div className={cn('rounded-xl border p-3 w-full', severityBg[message.insightCard.severity ?? 'info'])}>
            <p className="text-xs font-semibold text-content-primary mb-2">{message.insightCard.title}</p>
            {message.insightCard.metrics && (
              <div className="flex flex-col gap-1 mb-2">
                {message.insightCard.metrics.map((m, i) => (
                  <div key={i} className="flex justify-between gap-2 text-xs">
                    <span className="text-content-secondary">{m.label}</span>
                    <span className="text-content-primary font-medium text-right">{m.value}</span>
                  </div>
                ))}
              </div>
            )}
            {message.insightCard.recommendation && (
              <p className="text-xs text-content-secondary border-t border-surface-border pt-2 mt-1">
                → {message.insightCard.recommendation}
              </p>
            )}
          </div>
        )}

        <p className="text-xs text-content-tertiary px-1">{formatRelativeTime(message.timestamp)}</p>
      </div>
    </div>
  )
}
