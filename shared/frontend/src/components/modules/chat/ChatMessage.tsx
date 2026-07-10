import { Brain } from 'lucide-react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import type { Components } from 'react-markdown'
import { cn } from '../../../utils/cn'
import type { ChatMessage as ChatMessageType } from '../../../types/chat'
import { formatRelativeTime } from '../../../utils/formatters'

const severityBg: Record<string, string> = {
  critical: 'border-red-800 bg-red-900/20 light:border-red-200 light:bg-red-50',
  warning: 'border-amber-800 bg-amber-900/20 light:border-amber-200 light:bg-amber-50',
  positive: 'border-green-800 bg-green-900/20 light:border-emerald-200 light:bg-emerald-50',
  info: 'border-blue-800 bg-blue-900/20 light:border-cyan-200 light:bg-cyan-50',
}

const markdownComponents: Components = {
  p: ({ children }) => <p className="mb-2 last:mb-0">{children}</p>,
  strong: ({ children }) => <strong className="font-semibold text-content-primary">{children}</strong>,
  em: ({ children }) => <em className="italic">{children}</em>,
  a: ({ children, href }) => (
    <a href={href} target="_blank" rel="noopener noreferrer" className="text-brand-blue underline underline-offset-2 hover:opacity-80">
      {children}
    </a>
  ),
  ul: ({ children }) => <ul className="mb-2 list-disc space-y-1 pl-4 last:mb-0">{children}</ul>,
  ol: ({ children }) => <ol className="mb-2 list-decimal space-y-1 pl-4 last:mb-0">{children}</ol>,
  li: ({ children }) => <li className="pl-0.5">{children}</li>,
  h1: ({ children }) => <h1 className="mb-1.5 text-base font-semibold text-content-primary">{children}</h1>,
  h2: ({ children }) => <h2 className="mb-1.5 text-sm font-semibold text-content-primary">{children}</h2>,
  h3: ({ children }) => <h3 className="mb-1 text-sm font-semibold text-content-primary">{children}</h3>,
  blockquote: ({ children }) => (
    <blockquote className="mb-2 border-l-2 border-brand-blue/50 pl-2 italic text-content-secondary last:mb-0">
      {children}
    </blockquote>
  ),
  code: ({ children, className }) => {
    const isBlock = /language-/.test(className ?? '')
    if (isBlock) {
      return <code className={cn('font-mono text-xs', className)}>{children}</code>
    }
    return (
      <code className="rounded bg-surface-muted px-1 py-0.5 font-mono text-[0.8em] text-content-primary">
        {children}
      </code>
    )
  },
  pre: ({ children }) => (
    <pre className="mb-2 overflow-x-auto rounded-lg border border-surface-border bg-surface-muted p-2 text-xs last:mb-0">
      {children}
    </pre>
  ),
  table: ({ children }) => (
    <div className="mb-2 overflow-x-auto last:mb-0">
      <table className="w-full border-collapse text-xs">{children}</table>
    </div>
  ),
  th: ({ children }) => (
    <th className="border border-surface-border bg-surface-muted px-2 py-1 text-left font-semibold text-content-primary">
      {children}
    </th>
  ),
  td: ({ children }) => <td className="border border-surface-border px-2 py-1">{children}</td>,
  hr: () => <hr className="my-2 border-surface-border" />,
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
              ? 'bg-brand-blue-dim text-content-primary rounded-tr-sm whitespace-pre-wrap'
              : 'bg-surface text-content-primary rounded-tl-sm border border-surface-border'
          )}
        >
          {isUser ? (
            message.content
          ) : (
            <ReactMarkdown remarkPlugins={[remarkGfm]} components={markdownComponents}>
              {message.content}
            </ReactMarkdown>
          )}
        </div>

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
