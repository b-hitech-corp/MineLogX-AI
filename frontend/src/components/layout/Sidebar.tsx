import {
  LayoutDashboard,
  Truck,
  Wrench,
  BarChart3,
  Package,
  Fuel,
  MapPin,
  Shield,
} from 'lucide-react'
import type { LucideIcon } from 'lucide-react'
import { cn } from '../../utils/cn'
import { useApp } from '../../context/AppContext'
import type { ActiveModule } from '../../context/AppContext'

interface NavItem {
  id: ActiveModule
  label: string
  icon: LucideIcon
}

const navItems: NavItem[] = [
  { id: 'overview', label: 'Overview', icon: LayoutDashboard },
  { id: 'fleet', label: 'Fleet', icon: Truck },
  { id: 'maintenance', label: 'Maintenance', icon: Wrench },
  { id: 'kpis', label: 'KPIs', icon: BarChart3 },
  { id: 'load-tonnage', label: 'Load & Tonnage', icon: Package },
  { id: 'fuel', label: 'Fuel', icon: Fuel },
  { id: 'gps', label: 'GPS / Location', icon: MapPin },
  { id: 'safety', label: 'Safety', icon: Shield },
]

export function Sidebar() {
  const { activeModule, setActiveModule } = useApp()

  return (
    <aside className="flex h-full w-56 flex-col border-r border-surface-border bg-surface-card">
      <div className="flex h-14 items-center border-b border-surface-border px-4">
        <span className="text-lg font-bold tracking-tight text-content-primary">
          Mine<span className="text-brand-blue">LogX</span>
        </span>
      </div>

      <nav className="flex flex-1 flex-col gap-1 p-2 pt-3">
        {navItems.map(({ id, label, icon: Icon }) => (
          <button
            key={id}
            onClick={() => setActiveModule(id)}
            className={cn(
              'flex w-full items-center gap-3 rounded-lg px-3 py-2.5 text-sm font-medium transition-colors cursor-pointer text-left',
              activeModule === id
                ? 'bg-brand-blue-dim text-brand-blue'
                : 'text-content-secondary hover:bg-surface-muted hover:text-content-primary'
            )}
          >
            <Icon size={16} />
            {label}
          </button>
        ))}
      </nav>

      <div className="border-t border-surface-border p-3">
        <p className="text-xs text-content-tertiary">MineLogX v0.1 — Demo</p>
      </div>
    </aside>
  )
}
