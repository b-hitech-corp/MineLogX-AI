import { createPortal } from 'react-dom'
import { X, User, Sun, Moon } from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useApp } from '../../context/AppContext'
import type { ActiveModule } from '../../context/AppContext'
import { useTheme } from '../../context/ThemeContext'
import { CompanySelector } from './CompanySelector'

interface NavItem {
  id: ActiveModule
  label: string
  icon: LucideIcon
}

interface MobileMenuProps {
  isOpen: boolean
  onClose: () => void
  navItems: NavItem[]
  clock: { hh: string; mm: string; ss: string }
  shiftLabel: string
  shiftColor: string
}

export function MobileMenu({ isOpen, onClose, navItems, clock, shiftLabel, shiftColor }: MobileMenuProps) {
  const { activeModule, setActiveModule, currentUser } = useApp()
  const { isDark, toggleTheme } = useTheme()

  return createPortal(
    <>
      {/* Backdrop */}
      <div
        className={`fixed inset-0 z-40 bg-black/40 backdrop-blur-[2px] transition-opacity duration-300 md:hidden ${
          isOpen ? 'opacity-100' : 'pointer-events-none opacity-0'
        }`}
        onClick={onClose}
      />

      {/* Panel */}
      <div
        className={`fixed inset-y-0 left-0 z-50 flex w-72 max-w-[85%] flex-col border-r border-glass-border bg-glass-edge shadow-2xl backdrop-blur-xl transition-transform duration-300 ease-out md:hidden ${
          isOpen ? 'translate-x-0' : '-translate-x-full'
        }`}
      >
        <div className="flex h-14 shrink-0 items-center justify-between border-b border-glass-border px-4">
          <span
            className="text-[10px] font-semibold uppercase tracking-[0.15em] text-content-tertiary"
            style={{ fontFamily: 'var(--font-mono)' }}
          >
            Menu
          </span>
          <button
            onClick={onClose}
            aria-label="Close menu"
            className="rounded-lg p-1.5 text-content-secondary transition-colors hover:bg-surface-muted hover:text-content-primary cursor-pointer"
          >
            <X size={16} />
          </button>
        </div>

        <nav className="flex flex-col gap-1 overflow-y-auto p-3">
          {navItems.map(({ id, label, icon: Icon }) => {
            const isActive = activeModule === id
            return (
              <button
                key={id}
                onClick={() => { setActiveModule(id); onClose() }}
                className={`flex items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors cursor-pointer ${
                  isActive
                    ? 'bg-brand-blue/10 text-brand-blue font-semibold'
                    : 'text-content-secondary hover:bg-glass hover:text-content-primary'
                }`}
              >
                <Icon size={15} className="shrink-0" />
                {label}
              </button>
            )
          })}
        </nav>

        <div className="mx-3 border-t border-glass-border" />

        <div className="flex flex-col gap-3 p-3">
          <CompanySelector />

          {/* User pill + theme toggle */}
          <div className="flex items-center justify-between gap-2 rounded-xl border border-glass-border bg-glass px-2.5 py-1.5 backdrop-blur-md">
            <div className="flex items-center gap-2">
              <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-blue-dim">
                <User size={11} className="text-brand-blue" />
              </div>
              <span className="text-xs font-medium text-content-secondary">{currentUser}</span>
            </div>
            <button
              onClick={toggleTheme}
              title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
              className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-glass-border text-content-tertiary transition-colors hover:border-brand-blue hover:text-brand-blue cursor-pointer"
            >
              {isDark ? <Sun size={14} /> : <Moon size={14} />}
            </button>
          </div>

          {/* Clock + shift + pulse */}
          <div className="flex items-center justify-between gap-2 px-1">
            <div className="flex items-center gap-1.5">
              <span
                className="text-xs font-medium tabular-nums text-content-primary"
                style={{ fontFamily: 'var(--font-mono)' }}
              >
                {clock.hh}:{clock.mm}
                <span className="text-content-tertiary">:{clock.ss}</span>
              </span>
              <span
                className={`text-[10px] font-bold uppercase tracking-[0.1em] ${shiftColor}`}
                style={{ fontFamily: 'var(--font-mono)' }}
              >
                · {shiftLabel}
              </span>
            </div>
            <span className="relative flex h-1.5 w-1.5 shrink-0">
              <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-status-healthy opacity-60" />
              <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-status-healthy" />
            </span>
          </div>
        </div>
      </div>
    </>,
    document.body
  )
}
