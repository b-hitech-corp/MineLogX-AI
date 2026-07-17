import { ChevronDown, Check, Cpu } from 'lucide-react'
import { useRef, useState } from 'react'
import { useChat, CHAT_MODELS } from '../../../context/ChatContext'
import type { ChatModel } from '../../../context/ChatContext'
import { useClickOutside } from '../../../hooks/useClickOutside'

type DisplayBadge = 'DEFAULT' | 'CURRENT'

const badgeStyle: Record<DisplayBadge, string> = {
  DEFAULT: 'bg-brand-blue/15 text-brand-blue border-brand-blue/30',
  CURRENT: 'bg-green-900/30 text-green-400 border-green-700/40 light:bg-emerald-50 light:text-emerald-700 light:border-emerald-200',
}

const dotStyle: Record<DisplayBadge, string> = {
  DEFAULT: 'bg-brand-blue',
  CURRENT: 'bg-green-400 light:bg-emerald-500',
}

function getDisplayBadge(model: ChatModel, isSelected: boolean): DisplayBadge | undefined {
  if (model.badge === 'DEFAULT') return 'DEFAULT'
  if (isSelected) return 'CURRENT'
  return undefined
}

const CLOSE_DUR = 150 // matches --dropdown-close-dur

export function ModelSelector() {
  const { selectedModel, setSelectedModel } = useChat()
  const [open, setOpen] = useState(false)
  const [isClosing, setIsClosing] = useState(false)
  const ref = useRef<HTMLDivElement>(null)
  const currentBadge = getDisplayBadge(selectedModel, true) ?? 'CURRENT'

  function openDropdown() { setOpen(true) }
  function closeDropdown() {
    setIsClosing(true)
    setTimeout(() => { setOpen(false); setIsClosing(false) }, CLOSE_DUR)
  }
  function toggleDropdown() { if (open) closeDropdown(); else openDropdown() }

  useClickOutside(ref, closeDropdown)

  return (
    <div ref={ref} className="relative">
      {/* Upward dropdown */}
      {open && (
        <div
          className={`t-dropdown absolute bottom-full left-0 right-0 mb-2 rounded-2xl border border-glass-border bg-glass-edge shadow-2xl backdrop-blur-xl overflow-hidden ${isClosing ? 'is-closing' : 'is-open'}`}
          style={{ transformOrigin: 'bottom left' }}
        >
          <div className="border-b border-glass-border px-3 py-2 flex items-center gap-2">
            <Cpu size={11} className="text-content-tertiary" />
            <span
              className="text-[9px] font-semibold tracking-[0.16em] uppercase text-content-tertiary"
              style={{ fontFamily: 'var(--font-mono)' }}
            >
              Select Model
            </span>
          </div>

          <div className="py-1">
            {CHAT_MODELS.map((model) => {
              const isSelected = selectedModel.id === model.id
              const displayBadge = getDisplayBadge(model, isSelected)
              return (
                <button
                  key={model.id}
                  onClick={() => { setSelectedModel(model); closeDropdown() }}
                  className={`w-full flex items-center gap-3 px-3 py-2.5 text-left transition-colors cursor-pointer ${
                    isSelected
                      ? 'bg-brand-blue/8'
                      : 'hover:bg-glass'
                  }`}
                >
                  {/* Dot */}
                  <span className={`h-1.5 w-1.5 rounded-full shrink-0 ${displayBadge ? dotStyle[displayBadge] : 'bg-surface-muted'}`} />

                  {/* Info */}
                  <div className="flex-1 min-w-0">
                    <div className="flex items-center gap-2">
                      <span
                        className={`text-xs font-semibold leading-none ${isSelected ? 'text-content-primary' : 'text-content-secondary'}`}
                      >
                        {model.name}
                      </span>
                      {displayBadge && (
                        <span
                          className={`rounded border px-1.5 py-0.5 text-[8px] font-bold tracking-[0.1em] ${badgeStyle[displayBadge]}`}
                          style={{ fontFamily: 'var(--font-mono)' }}
                        >
                          {displayBadge}
                        </span>
                      )}
                    </div>
                    <p
                      className="mt-0.5 text-[10px] text-content-tertiary truncate"
                      style={{ fontFamily: 'var(--font-mono)' }}
                    >
                      {model.description}
                    </p>
                  </div>

                  {/* Checkmark */}
                  {isSelected && <Check size={12} className="text-brand-blue shrink-0" />}
                </button>
              )
            })}
          </div>
        </div>
      )}

      {/* Trigger */}
      <button
        onClick={toggleDropdown}
        className={`w-full flex items-center gap-2 rounded-xl border px-3 py-2 text-xs backdrop-blur-md transition-all duration-150 cursor-pointer ${
          open
            ? 'border-brand-blue/40 bg-brand-blue/10 text-content-primary'
            : 'border-glass-border bg-glass text-content-secondary hover:bg-glass-edge hover:text-content-primary'
        }`}
      >
        <Cpu size={12} className={open ? 'text-brand-blue' : 'text-content-tertiary'} />

        <span
          className="text-[9px] font-semibold tracking-[0.14em] uppercase text-content-tertiary shrink-0"
          style={{ fontFamily: 'var(--font-mono)' }}
        >
          Model
        </span>

        <span className="font-semibold text-content-primary flex-1 text-left">{selectedModel.name}</span>

        <span
          className={`rounded border px-1.5 py-0.5 text-[8px] font-bold tracking-[0.08em] ${badgeStyle[currentBadge]}`}
          style={{ fontFamily: 'var(--font-mono)' }}
        >
          {currentBadge}
        </span>

        <ChevronDown
          size={11}
          className={`text-content-tertiary transition-transform duration-150 shrink-0 ${open && !isClosing ? 'rotate-180' : ''}`}
        />
      </button>
    </div>
  )
}
