import logo from '../../assets/logo.webp'
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
  Sun,
  Moon,
  Menu,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { useEffect, useState } from 'react'
import { cn } from '../../utils/cn'
import { useApp } from '../../context/AppContext'
import type { ActiveModule } from '../../context/AppContext'
import { useTheme } from '../../context/ThemeContext'
import { AlertButton } from './AlertButton'
import { CompanySelector } from './CompanySelector'
import { MobileMenu } from './MobileMenu'

interface NavItem {
  id: ActiveModule
  label: string
  icon: LucideIcon
}

const navItems: NavItem[] = [
  { id: 'overview',     label: 'Overview',      icon: LayoutDashboard },
  { id: 'fleet',        label: 'Fleet',         icon: Truck },
  { id: 'maintenance',  label: 'Maintenance',   icon: Wrench },
  { id: 'kpis',         label: 'KPIs',          icon: BarChart3 },
  { id: 'load-tonnage', label: 'Load & Tonnage',icon: Package },
  { id: 'fuel',         label: 'Fuel',          icon: Fuel },
  { id: 'gps',          label: 'GPS / Location',icon: MapPin },
  { id: 'safety',       label: 'Safety',        icon: Shield },
]

export function Header() {
  const { activeModule, setActiveModule, currentShift, currentUser } = useApp()
  const { isDark, toggleTheme } = useTheme()
  const [now, setNow] = useState(() => new Date())
  const [isMobileMenuOpen, setMobileMenuOpen] = useState(false)

  useEffect(() => {
    const id = setInterval(() => setNow(new Date()), 1000)
    return () => clearInterval(id)
  }, [])

  const hh = now.getHours().toString().padStart(2, '0')
  const mm = now.getMinutes().toString().padStart(2, '0')
  const ss = now.getSeconds().toString().padStart(2, '0')

  const shiftLabel = currentShift.includes('—')
    ? currentShift.split('—')[0].trim().replace(' Shift', '')
    : currentShift.replace(' Shift', '')

  const shiftColor = currentShift.toLowerCase().includes('day')
    ? 'text-status-warning'
    : 'text-blue-400'

  return (
    <header className="sticky top-0 z-30 flex h-14 shrink-0 items-stretch border-b border-glass-border bg-glass-edge backdrop-blur-xl">

      {/* ── Mobile bar (<768px): hamburger · icon · alerts ── */}
      <div className="flex w-full items-center justify-between px-4 md:hidden">
        <button
          onClick={() => setMobileMenuOpen(true)}
          aria-label="Open menu"
          className="flex h-9 w-9 items-center justify-center rounded-xl text-content-secondary transition-colors hover:bg-surface-muted hover:text-content-primary cursor-pointer"
        >
          <Menu size={20} />
        </button>

        <img
          src={logo}
          alt="MLX Ai"
          className="h-8 w-auto object-contain"
          style={{ filter: isDark ? 'invert(1) hue-rotate(180deg)' : undefined }}
        />

        <AlertButton compact />
      </div>

      <MobileMenu
        isOpen={isMobileMenuOpen}
        onClose={() => setMobileMenuOpen(false)}
        navItems={navItems}
        clock={{ hh, mm, ss }}
        shiftLabel={shiftLabel}
        shiftColor={shiftColor}
      />

      {/* ── Desktop bar (≥768px) ── */}
      <div className="hidden md:contents">

      {/* ── Logo ── */}
      <div className="flex shrink-0 items-center px-4">
        <img
          src={logo}
          alt="MLX Ai"
          className="h-12 w-auto object-contain"
          style={{ filter: isDark ? 'invert(1) hue-rotate(180deg)' : undefined }}
        />
      </div>

      {/* Separator */}
      <div className="my-3 w-px shrink-0 bg-glass-border" />

      {/* ── Nav ── */}
      <nav className="scrollbar-none flex flex-1 items-stretch overflow-x-auto">
        {navItems.map(({ id, label, icon: Icon }) => {
          const isActive = activeModule === id
          return (
            <button
              key={id}
              onClick={() => setActiveModule(id)}
              className={cn(
                'relative flex shrink-0 items-center gap-1.5 whitespace-nowrap px-3.5 text-xs font-medium transition-colors duration-150 cursor-pointer',
                isActive
                  ? 'text-brand-blue font-semibold'
                  : 'text-content-secondary hover:text-content-primary'
              )}
            >
              <Icon size={13} className="shrink-0" />
              {label}
              {isActive && (
                <span className="absolute bottom-0 left-3 right-3 h-0.5 rounded-full bg-brand-blue" />
              )}
            </button>
          )
        })}
      </nav>

      {/* Separator */}
      <div className="my-3 w-px shrink-0 bg-glass-border" />

      {/* ── Controls ── */}
      <div className="flex shrink-0 items-center gap-2 px-4">
        <AlertButton />
        <CompanySelector />

        {/* User pill */}
        <div className="flex items-center gap-2 rounded-xl border border-glass-border bg-glass px-2.5 py-1.5 backdrop-blur-md">
          <div className="flex h-5 w-5 shrink-0 items-center justify-center rounded-full bg-brand-blue-dim">
            <User size={11} className="text-brand-blue" />
          </div>
          <span className="hidden sm:inline text-xs font-medium text-content-secondary">{currentUser}</span>
        </div>

        {/* Theme toggle */}
        <button
          onClick={toggleTheme}
          title={isDark ? 'Switch to light mode' : 'Switch to dark mode'}
          className="flex h-8 w-8 shrink-0 items-center justify-center rounded-xl border border-glass-border text-content-tertiary transition-colors hover:border-brand-blue hover:text-brand-blue cursor-pointer"
        >
          {isDark ? <Sun size={14} /> : <Moon size={14} />}
        </button>

        {/* Separator */}
        <div className="hidden lg:block h-5 w-px shrink-0 bg-glass-border" />

        {/* Clock + shift + pulse */}
        <div className="hidden lg:flex items-center gap-2">
          <div className="flex items-center gap-1.5">
            <span
              className="text-xs font-medium tabular-nums text-content-primary"
              style={{ fontFamily: 'var(--font-mono)' }}
            >
              {hh}:{mm}
              <span className="text-content-tertiary">:{ss}</span>
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
    </header>
  )
}
