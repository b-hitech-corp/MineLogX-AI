import { Bell } from 'lucide-react'
import { useAlerts } from '../../context/AlertsContext'
import { useChat } from '../../context/ChatContext'

export function AlertButton() {
  const { criticalCount, activeCount } = useAlerts()
  const { openChat } = useChat()

  if (activeCount === 0) return null

  return (
    <button
      onClick={openChat}
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
  )
}
