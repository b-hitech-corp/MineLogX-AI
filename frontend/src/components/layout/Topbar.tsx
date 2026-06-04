import { Bell, Building2, ChevronDown, User } from 'lucide-react'
import { useRef, useState } from 'react'
import { useAlerts } from '../../context/AlertsContext'
import { useApp, COMPANIES } from '../../context/AppContext'
import { useChat } from '../../context/ChatContext'
import { useClickOutside } from '../../hooks/useClickOutside'

export function Topbar() {
  const { criticalCount, activeCount } = useAlerts()
  const { currentShift, currentUser, selectedCompany, selectCompany } = useApp()
  const { openChat } = useChat()
  const [dropdownOpen, setDropdownOpen] = useState(false)
  const dropdownRef = useRef<HTMLDivElement>(null)

  useClickOutside(dropdownRef, () => setDropdownOpen(false))

  return (
    <header className="flex h-14 items-center justify-between border-b border-surface-border bg-surface-card px-6">
      <div className="flex items-center gap-3">
        <span className="text-xs text-content-secondary">{currentShift}</span>
        <span className="h-4 w-px bg-surface-border" />
        <span className="text-xs text-content-tertiary">
          {new Date().toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short', year: 'numeric' })}
        </span>
      </div>

      <div className="flex items-center gap-3">
        {/* Company selector */}
        <div ref={dropdownRef} className="relative">
          <button
            onClick={() => setDropdownOpen((o) => !o)}
            className="flex items-center gap-2 rounded-lg border border-surface-border px-3 py-1.5 text-xs text-content-secondary hover:bg-surface-muted transition-colors cursor-pointer"
          >
            <Building2 size={14} className="text-brand-blue" />
            <span className="text-content-primary font-medium">{selectedCompany.name}</span>
            <ChevronDown size={12} className={`text-content-tertiary transition-transform ${dropdownOpen ? 'rotate-180' : ''}`} />
          </button>

          {dropdownOpen && (
            <div className="absolute right-0 top-full mt-1 z-50 min-w-[180px] rounded-lg border border-surface-border bg-surface-card shadow-lg py-1">
              {COMPANIES.map((company) => (
                <button
                  key={company.id}
                  onClick={() => { selectCompany(company); setDropdownOpen(false) }}
                  className={`w-full flex items-center justify-between px-3 py-2 text-xs transition-colors cursor-pointer ${
                    selectedCompany.id === company.id
                      ? 'bg-brand-blue/10 text-brand-blue'
                      : 'text-content-secondary hover:bg-surface-muted hover:text-content-primary'
                  }`}
                >
                  <span>{company.name}</span>
                </button>
              ))}
            </div>
          )}
        </div>

        {activeCount > 0 && (
          <button
            onClick={openChat}
            className="relative flex items-center gap-2 rounded-lg border border-surface-border px-3 py-1.5 text-xs text-content-secondary hover:bg-surface-muted transition-colors cursor-pointer"
          >
            <Bell size={14} className={criticalCount > 0 ? 'text-status-critical' : 'text-status-warning'} />
            <span>{activeCount} alert{activeCount !== 1 ? 's' : ''}</span>
            {criticalCount > 0 && (
              <span className="absolute -top-1.5 -right-1.5 flex h-4 w-4 items-center justify-center rounded-full bg-status-critical text-[10px] font-bold text-white">
                {criticalCount}
              </span>
            )}
          </button>
        )}

        <div className="flex items-center gap-2 rounded-lg border border-surface-border px-3 py-1.5">
          <User size={14} className="text-content-secondary" />
          <span className="text-xs text-content-secondary">{currentUser}</span>
        </div>
      </div>
    </header>
  )
}
