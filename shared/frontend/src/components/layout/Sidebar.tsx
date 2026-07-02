import {
  LayoutDashboard,
  Truck,
  Wrench,
  BarChart3,
  Package,
  Fuel,
  MapPin,
  Shield,
  User,
  Activity,
  Sun,
  Moon,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useEffect, useState } from 'react'
import { cn } from '../../utils/cn'
import { useTheme } from '../../context/ThemeContext'
import { useApp } from '../../context/AppContext'
import type { ActiveModule } from '../../context/AppContext'

interface NavItem {
  id: ActiveModule
  label: string
  icon: LucideIcon
  section?: string
}

const navItems: NavItem[] = [
  { id: 'overview',      label: 'Overview',      icon: LayoutDashboard, section: 'Operations' },
  { id: 'fleet',         label: 'Fleet',          icon: Truck },
  { id: 'maintenance',   label: 'Maintenance',    icon: Wrench },
  { id: 'fuel',          label: 'Fuel',           icon: Fuel },
  { id: 'load-tonnage',  label: 'Load & Tonnage', icon: Package,        section: 'Analytics' },
  { id: 'kpis',          label: 'KPIs',           icon: BarChart3 },
  { id: 'gps',           label: 'GPS / Location', icon: MapPin,         section: 'Intelligence' },
  { id: 'safety',        label: 'Safety',         icon: Shield },
]

function SidebarClock() {
  const [now, setNow] = useState(() => new Date())

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  const hh = now.getHours().toString().padStart(2, '0')
  const mm = now.getMinutes().toString().padStart(2, '0')
  const ss = now.getSeconds().toString().padStart(2, '0')
  const date = now.toLocaleDateString('en-AU', { weekday: 'short', day: 'numeric', month: 'short' })

  return { hh, mm, ss, date }
}

export function Sidebar() {
  const { activeModule, setActiveModule, currentShift, currentUser } = useApp()
  const { isDark, toggleTheme } = useTheme()
  const { hh, mm, ss, date } = SidebarClock()

  const shiftColor =
    currentShift.toLowerCase().includes('day')
      ? 'text-status-warning bg-amber-900/20 border-amber-800/40'
      : currentShift.toLowerCase().includes('night')
      ? 'text-blue-400 bg-blue-900/20 border-blue-800/40'
      : 'text-content-secondary bg-surface-muted border-surface-border'

  const shiftLabel = currentShift.includes('—')
    ? currentShift.split('—')[0].trim()
    : currentShift

  return (
    <aside className="flex h-full w-56 flex-col border-r border-glass-border bg-glass-edge backdrop-blur-xl">

      {/* Logo */}
      <div className="flex h-16 items-center border-b border-glass-border px-5">
        <div className="flex flex-col gap-0.5">
          <span
            className="text-[22px] font-black leading-none tracking-tight text-content-primary uppercase"
            style={{ fontFamily: 'var(--font-display)' }}
          >
            Mine<span className="text-brand-blue">Log</span>X
          </span>
          <span
            className="text-[9px] font-medium tracking-[0.18em] uppercase text-content-tertiary"
            style={{ fontFamily: 'var(--font-mono)' }}
          >
            Operational Intel
          </span>
        </div>
      </div>

      {/* User */}
      <div className="flex items-center gap-2.5 border-b border-glass-border px-4 py-3">
        <div className="flex h-7 w-7 shrink-0 items-center justify-center rounded-full bg-brand-blue-dim">
          <User size={13} className="text-brand-blue" />
        </div>
        <div className="min-w-0">
          <p className="truncate text-xs font-semibold leading-none text-content-primary">
            {currentUser}
          </p>
          <p
            className="mt-0.5 text-[9px] tracking-[0.1em] uppercase text-content-tertiary"
            style={{ fontFamily: 'var(--font-mono)' }}
          >
            Operator
          </p>
        </div>
      </div>

      {/* Nav */}
      <nav className="flex flex-1 flex-col gap-0 overflow-y-auto px-2 py-3">
        {navItems.map(({ id, label, icon: Icon, section }, idx) => {
          const isActive = activeModule === id
          const prevSection = idx > 0 ? navItems[idx - 1].section : undefined
          const showLabel = section !== undefined && section !== prevSection

          return (
            <div key={id}>
              {showLabel && (
                <div className="px-2 pb-1 pt-3">
                  <span
                    className="text-[9px] font-semibold tracking-[0.16em] uppercase text-content-tertiary"
                    style={{ fontFamily: 'var(--font-mono)' }}
                  >
                    {section}
                  </span>
                </div>
              )}
              <button
                onClick={() => setActiveModule(id)}
                className={cn(
                  'relative flex w-full items-center gap-2.5 rounded-lg px-3 py-2.5 text-sm transition-all duration-100 cursor-pointer text-left',
                  isActive
                    ? 'bg-brand-blue/10 text-brand-blue font-semibold'
                    : 'text-content-secondary font-medium hover:bg-surface-muted/60 hover:text-content-primary'
                )}
              >
                {isActive && (
                  <span className="absolute left-0 top-1/2 -translate-y-1/2 h-5 w-[3px] rounded-r-full bg-brand-blue" />
                )}
                <Icon size={14} className="shrink-0" />
                <span className="tracking-[0.01em]">{label}</span>
              </button>
            </div>
          )
        })}
      </nav>

      {/* Date / Time / Shift */}
      <div className="border-t border-surface-border px-4 py-3 space-y-2.5">
        {/* Clock row */}
        <div className="flex items-center justify-between">
          <span
            className="text-[10px] text-content-tertiary tracking-wide"
            style={{ fontFamily: 'var(--font-mono)' }}
          >
            {date}
          </span>
          <span
            className="text-[11px] font-medium tabular-nums text-content-primary"
            style={{ fontFamily: 'var(--font-mono)' }}
          >
            {hh}:{mm}
            <span className="text-content-tertiary">:{ss}</span>
          </span>
        </div>

        {/* Shift + theme toggle + system status */}
        <div className="flex items-center justify-between">
          <span
            className={`rounded-md border px-2 py-0.5 text-[9px] font-semibold uppercase tracking-[0.12em] ${shiftColor}`}
            style={{ fontFamily: 'var(--font-mono)' }}
          >
            {shiftLabel}
          </span>
          <div className="flex items-center gap-2">
            <button
              onClick={toggleTheme}
              title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
              className="flex h-6 w-6 items-center justify-center rounded-md border border-surface-border text-content-tertiary transition-colors hover:border-brand-blue hover:text-brand-blue cursor-pointer"
            >
              {isDark ? <Sun size={11} /> : <Moon size={11} />}
            </button>
            <div className="flex items-center gap-1.5">
              <span className="relative flex h-1.5 w-1.5">
                <span className="animate-ping absolute inline-flex h-full w-full rounded-full bg-status-healthy opacity-60" />
                <span className="relative inline-flex h-1.5 w-1.5 rounded-full bg-status-healthy" />
              </span>
              <Activity size={9} className="text-content-tertiary" />
            </div>
          </div>
        </div>
      </div>
    </aside>
  )
}
