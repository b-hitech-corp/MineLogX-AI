import { Bell } from 'lucide-react'
import { useEffect, useRef, useState } from 'react'
import { useAlerts } from '../../context/AlertsContext'
import { useClickOutside } from '../../hooks/useClickOutside'
import { AlertsMenu } from './AlertsMenu'
import { AlertsPanel } from './AlertsPanel'

interface AlertButtonProps {
  /** Icon-only variant used in the mobile header bar — always visible, opens a full-height panel. */
  compact?: boolean
}

export function AlertButton({ compact = false }: AlertButtonProps = {}) {
  const { criticalCount, activeCount } = useAlerts()
  const [isOpen, setIsOpen] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  // AlertsPanel (mobile) is portaled to <body>, so it falls outside `ref`'s DOM subtree —
  // its own backdrop already handles close-on-outside-click, so skip this for the compact variant.
  useClickOutside(ref, () => { if (!compact) setIsOpen(false) })

  useEffect(() => {
    if (!compact && activeCount === 0) setIsOpen(false)
  }, [activeCount, compact])

  useEffect(() => {
    if (!isOpen) return
    function onKeyDown(e: KeyboardEvent) {
      if (e.key === 'Escape') setIsOpen(false)
    }
    document.addEventListener('keydown', onKeyDown)
    return () => document.removeEventListener('keydown', onKeyDown)
  }, [isOpen])

  if (compact) {
    return (
      <div ref={ref} className="relative">
        <button
          onClick={() => setIsOpen((o) => !o)}
          aria-label="View alerts"
          className={`relative flex h-9 w-9 items-center justify-center rounded-xl transition-colors cursor-pointer ${
            criticalCount > 0
              ? 'text-red-400 hover:bg-red-900/20'
              : activeCount > 0
                ? 'text-amber-400 hover:bg-amber-900/15'
                : 'text-content-secondary hover:bg-surface-muted'
          }`}
        >
          <Bell size={18} />
          {activeCount > 0 && (
            <span
              className={`absolute -right-0.5 -top-0.5 flex h-4 min-w-4 items-center justify-center rounded-full px-1 text-[9px] font-bold text-white ${
                criticalCount > 0 ? 'bg-status-critical' : 'bg-amber-500'
              }`}
            >
              {activeCount}
            </span>
          )}
        </button>
        {isOpen && <AlertsPanel onClose={() => setIsOpen(false)} />}
      </div>
    )
  }

  if (activeCount === 0) return null

  return (
    <div ref={ref} className="relative">
      <button
        onClick={() => setIsOpen((o) => !o)}
        className={`relative flex items-center gap-2 rounded-xl border px-3 py-1.5 text-xs backdrop-blur-md transition-colors cursor-pointer ${
          criticalCount > 0
            ? 'border-red-500/30 bg-red-900/20 text-red-400 hover:bg-red-900/30 light:bg-red-50 light:text-red-600 light:border-red-300 light:hover:bg-red-100'
            : 'border-amber-500/30 bg-amber-900/15 text-amber-400 hover:bg-amber-900/25 light:bg-amber-50 light:text-amber-600 light:border-amber-300 light:hover:bg-amber-100'
        }`}
      >
        <Bell size={13} />
        <span className="font-semibold tabular-nums" style={{ fontFamily: 'var(--font-mono)' }}>
          {activeCount}
        </span>
        <span className="text-[10px] font-medium opacity-80">
          {activeCount === 1 ? 'alert' : 'alerts'}
        </span>
        {criticalCount > 0 && (
          <span className="absolute -right-1.5 -top-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-status-critical text-[9px] font-bold text-white">
            {criticalCount}
          </span>
        )}
      </button>
      {isOpen && <AlertsMenu />}
    </div>
  )
}
