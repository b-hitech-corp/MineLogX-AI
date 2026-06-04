import { X, MessageSquare } from 'lucide-react'
import { useChat } from '../../../context/ChatContext'
import { ChatMessage } from './ChatMessage'
import { ChatInput } from './ChatInput'
import { useEffect, useRef } from 'react'

export function ChatPanel() {
  const { isOpen, closeChat, messages, sendMessage, isTyping } = useChat()
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, isTyping])

  if (!isOpen) return null

  return (
    <div className="fixed inset-y-0 right-0 z-50 flex w-[420px] flex-col border-l border-surface-border bg-surface-card shadow-2xl">
      <div className="flex h-14 items-center justify-between border-b border-surface-border px-4">
        <div className="flex items-center gap-2">
          <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-blue-dim">
            <MessageSquare size={14} className="text-brand-blue" />
          </div>
          <div>
            <p className="text-sm font-semibold text-content-primary">MineLogX AI</p>
            <p className="text-xs text-status-healthy">● Online</p>
          </div>
        </div>
        <button
          onClick={closeChat}
          className="rounded-lg p-1.5 hover:bg-surface-muted transition-colors cursor-pointer text-content-secondary"
        >
          <X size={16} />
        </button>
      </div>

      <div className="flex-1 overflow-y-auto p-4 flex flex-col gap-4">
        {messages.map((msg) => (
          <ChatMessage key={msg.id} message={msg} />
        ))}
        {isTyping && (
          <div className="flex items-center gap-2 text-content-tertiary text-xs">
            <div className="flex gap-1">
              <span className="animate-bounce delay-0 h-1.5 w-1.5 rounded-full bg-brand-blue" />
              <span className="animate-bounce delay-75 h-1.5 w-1.5 rounded-full bg-brand-blue" style={{ animationDelay: '0.1s' }} />
              <span className="animate-bounce delay-150 h-1.5 w-1.5 rounded-full bg-brand-blue" style={{ animationDelay: '0.2s' }} />
            </div>
            AI is thinking…
          </div>
        )}
        <div ref={bottomRef} />
      </div>

      <div className="border-t border-surface-border p-4">
        <ChatInput onSend={sendMessage} disabled={isTyping} />
      </div>
    </div>
  )
}
