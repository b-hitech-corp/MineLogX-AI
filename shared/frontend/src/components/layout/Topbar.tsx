import { Bell, Building2, ChevronDown, User, Clock } from 'lucide-react'
import { useRef, useState, useEffect } from 'react'
import { useAlerts } from '../../context/AlertsContext'
import { useApp, COMPANIES } from '../../context/AppContext'
import { useChat } from '../../context/ChatContext'
import { useClickOutside } from '../../hooks/useClickOutside'

function LiveClock() {
  const [time, setTime] = useState(() => new Date())

  useEffect(() => {
    const id = setInterval(() => setTime(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  const hh = time.getHours().toString().padStart(2, '0')
  const mm = time.getMinutes().toString().padStart(2, '0')
  const ss = time.getSeconds().toString().padStart(2, '0')
  const date = time.toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short' })

  return (
    <div className="flex items-center gap-2">
      <Clock size={12} className="text-content-tertiary" />
      <span
        className="text-xs text-content-secondary tracking-wide"
        style={{ fontFamily: 'var(--font-mono)' }}
      >
        {date}
      </span>
      <span className="h-3 w-px bg-surface-border" />
      <span
        className="text-xs font-medium text-content-primary tabular-nums"
        style={{ fontFamily: 'var(--font-mono)' }}
      >
        {hh}:{mm}
        <span className="text-content-tertiary">:{ss}</span>
      </span>
    </div>
  )
}

export function Topbar() {
  const { criticalCount, activeCount } = useAlerts()
  const { currentShift, currentUser, selectedCompany, selectCompany } = useApp()
  const { openChat } = useChat()
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  useClickOutside(dropdownRef, () => setDropdownOpen(false))

  const shiftColor =
    currentShift.toLowerCase().includes('day')
      ? 'text-status-warning bg-amber-900/20 border-amber-800/40'
      : currentShift.toLowerCase().includes('night')
      ? 'text-blue-400 bg-blue-900/20 border-blue-800/40'
      : 'text-content-secondary bg-surface-muted border-surface-border'

  return (
    <header className="flex h-14 items-center justify-between border-b border-surface-border bg-surface-card px-6">
      {/* Left: clock + shift */}
      <div className="flex items-center gap-4">
        <LiveClock />
        <span className="h-4 w-px bg-surface-border" />
        <span
          className={`rounded-md border px-2 py-0.5 text-[10px] font-semibold uppercase tracking-[0.12em] ${shiftColor}`}
          style={{ fontFamily: 'var(--font-mono)' }}
        >
          {currentShift}
        </span>
      </div>

      {/* Right: company + alerts + user */}
      <div className="flex items-center gap-2">
        {/* Company selector */}
        <div ref={dropdownRef} className="relative">
          <button
            onClick={() => setDropdownOpen((o) => !o)}
            className="flex items-center gap-2 rounded-lg border border-surface-border bg-surface px-3 py-1.5 text-xs transition-colors hover:border-surface-muted hover:bg-surface-muted cursor-pointer"
          >
            <Building2 size={13} className="text-brand-blue shrink-0" />
            <span className="font-semibold text-content-primary">{selectedCompany.name}</span>
            <ChevronDown
              size={11}
              className={`text-content-tertiary transition-transform duration-150 ${dropdownOpen ? 'rotate-180' : ''}`}
            />
          </button>

          {dropdownOpen && (
            <div className="absolute right-0 top-full z-50 mt-1.5 min-w-[196px] rounded-xl border border-surface-border bg-surface-card py-1 shadow-xl">
              <p
                className="px-3 pb-1 pt-2 text-[9px] font-semibold tracking-[0.15em] uppercase text-content-tertiary"
                style={{ fontFamily: 'var(--font-mono)' }}
              >
                Select Site
              </p>
              {COMPANIES.map((company) => (
                <button
                  key={company.id}
                  onClick={() => { selectCompany(company); setDropdownOpen(false) }}
                  className={`flex w-full items-center justify-between px-3 py-2 text-xs transition-colors cursor-pointer ${
                    selectedCompany.id === company.id
                      ? 'bg-brand-blue/10 text-brand-blue font-semibold'
                      : 'text-content-secondary hover:bg-surface-muted hover:text-content-primary'
                  }`}
                >
                  <span>{company.name}</span>
                  {selectedCompany.id === company.id && (
                    <span className="h-1.5 w-1.5 rounded-full bg-brand-blue" />
                  )}
                </button>
              ))}
            </div>
          )}
        </div>

        {/* Alert button */}
        {activeCount > 0 && (
          <button
            onClick={openChat}
            className={`relative flex items-center gap-2 rounded-lg border px-3 py-1.5 text-xs transition-colors cursor-pointer ${
              criticalCount > 0
                ? 'border-red-800/50 bg-red-900/15 text-red-400 hover:bg-red-900/25'
                : 'border-amber-800/40 bg-amber-900/10 text-amber-400 hover:bg-amber-900/20'
            }`}
          >
            <Bell size={13} />
            <span
              className="font-semibold tabular-nums"
              style={{ fontFamily: 'var(--font-mono)' }}
            >
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
        )}

        {/* User */}
        <div className="flex items-center gap-2 rounded-lg border border-surface-border px-3 py-1.5">
          <div className="flex h-5 w-5 items-center justify-center rounded-full bg-brand-blue-dim">
            <User size={11} className="text-brand-blue" />
          </div>
          <span className="text-xs font-medium text-content-secondary">{currentUser}</span>
        </div>
      </div>
    </header>
  )
}
