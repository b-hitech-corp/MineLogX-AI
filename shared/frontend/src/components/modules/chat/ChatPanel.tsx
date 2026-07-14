import { X, MessageSquare } from 'lucide-react'
import { useChat } from '../../../context/ChatContext'
import { ChatMessage } from './ChatMessage'
import { ChatInput } from './ChatInput'
import { ModelSelector } from './ModelSelector'
import { useEffect, useRef } from 'react'

export function ChatPanel() {
  const { isOpen, closeChat, messages, sendMessage, isTyping } = useChat()
  const bottomRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    if (isOpen) {
      bottomRef.current?.scrollIntoView({ behavior: 'smooth' })
    }
  }, [messages, isTyping, isOpen])

  return (
    <>
      {/* Backdrop */}
      <div
        className={`fixed inset-0 z-40 bg-black/40 backdrop-blur-[2px] transition-opacity duration-300 ${
          isOpen ? 'opacity-100' : 'opacity-0 pointer-events-none'
        }`}
        onClick={closeChat}
      />

      {/* Panel */}
      <div
        className={`fixed inset-y-0 right-0 z-50 flex w-full flex-col border-l border-glass-border bg-glass-edge shadow-2xl backdrop-blur-xl transition-transform duration-300 ease-out md:w-[420px] ${
          isOpen ? 'translate-x-0' : 'translate-x-full'
        }`}
      >
        {/* Header */}
        <div className="flex h-14 shrink-0 items-center justify-between border-b border-glass-border px-4">
          <div className="flex items-center gap-2.5">
            <div className="flex h-7 w-7 items-center justify-center rounded-lg bg-brand-blue-dim">
              <MessageSquare size={14} className="text-brand-blue" />
            </div>
            <div>
              <p className="text-sm font-semibold leading-none text-content-primary">MineLogX AI</p>
              <p className="mt-0.5 flex items-center gap-1 text-[10px] text-status-healthy" style={{ fontFamily: 'var(--font-mono)' }}>
                <span className="relative flex h-1.5 w-1.5">
                  <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-status-healthy opacity-60" />
                  <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-status-healthy" />
                </span>
                Online
              </p>
            </div>
          </div>
          <button
            onClick={closeChat}
            className="rounded-lg p-1.5 text-content-secondary transition-colors hover:bg-surface-muted hover:text-content-primary cursor-pointer"
          >
            <X size={16} />
          </button>
        </div>

        {/* Messages */}
        <div className="flex flex-1 flex-col gap-4 overflow-y-auto p-4">
          {messages.map((msg) => (
            <ChatMessage key={msg.id} message={msg} />
          ))}

          {isTyping && (
            <div className="flex items-center gap-2 text-xs text-content-tertiary">
              <div className="flex gap-1">
                {[0, 0.12, 0.24].map((delay, i) => (
                  <span
                    key={i}
                    className="h-1.5 w-1.5 animate-bounce rounded-full bg-brand-blue"
                    style={{ animationDelay: `${delay}s` }}
                  />
                ))}
              </div>
              AI is thinking…
            </div>
          )}

          <div ref={bottomRef} />
        </div>

        {/* Input + model selector */}
        <div className="shrink-0 border-t border-glass-border p-4 space-y-2">
          <ChatInput onSend={sendMessage} disabled={isTyping} />
          <ModelSelector />
        </div>
      </div>
    </>
  )
}
