import { Send } from 'lucide-react'
import { useState } from 'react'

interface ChatInputProps {
  onSend: (message: string) => void
  disabled?: boolean
}

export function ChatInput({ onSend, disabled }: ChatInputProps) {
  const [value, setValue] = useState('')

  function handleSubmit(e: React.FormEvent) {
    e.preventDefault()
    const trimmed = value.trim()
    if (!trimmed || disabled) return
    onSend(trimmed)
    setValue('')
  }

  function handleKeyDown(e: React.KeyboardEvent) {
    if (e.key === 'Enter' && !e.shiftKey) {
      e.preventDefault()
      handleSubmit(e as unknown as React.FormEvent)
    }
  }

  return (
    <form onSubmit={handleSubmit} className="flex gap-2">
      <input
        type="text"
        value={value}
        onChange={(e) => setValue(e.target.value)}
        onKeyDown={handleKeyDown}
        placeholder="Ask about fleet, fuel, maintenance…"
        disabled={disabled}
        className="flex-1 rounded-lg border border-surface-border bg-surface px-3 py-2 text-sm text-content-primary placeholder:text-content-tertiary focus:outline-none focus:border-brand-blue transition-colors disabled:opacity-50"
      />
      <button
        type="submit"
        disabled={disabled || !value.trim()}
        className="flex h-9 w-9 items-center justify-center rounded-lg bg-brand-blue hover:bg-blue-600 transition-colors cursor-pointer disabled:opacity-50 disabled:cursor-not-allowed"
      >
        <Send size={14} className="text-white" />
      </button>
    </form>
  )
}
