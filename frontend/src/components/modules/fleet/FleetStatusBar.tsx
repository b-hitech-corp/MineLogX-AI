import { Truck, Zap, Wrench, WifiOff } from 'lucide-react'
import type { FleetAsset } from '../../../types/fleet'

interface FleetStatusBarProps {
  assets: FleetAsset[]
}

export function FleetStatusBar({ assets }: FleetStatusBarProps) {
  const counts = {
    active: assets.filter((a) => a.status === 'active').length,
    idle: assets.filter((a) => a.status === 'idle').length,
    maintenance: assets.filter((a) => a.status === 'maintenance').length,
    offline: assets.filter((a) => a.status === 'offline').length,
  }

  return (
    <div className="grid grid-cols-2 gap-3 sm:grid-cols-4 sm:gap-4">
      {[
        { label: 'Active', count: counts.active, icon: Zap, color: 'text-status-healthy', bg: 'bg-green-900/20 border-green-800 light:bg-emerald-50 light:border-emerald-200' },
        { label: 'Idle', count: counts.idle, icon: Truck, color: 'text-status-warning', bg: 'bg-amber-900/20 border-amber-800 light:bg-amber-50 light:border-amber-200' },
        { label: 'Maintenance', count: counts.maintenance, icon: Wrench, color: 'text-blue-400 light:text-cyan-600', bg: 'bg-blue-900/20 border-blue-800 light:bg-cyan-50 light:border-cyan-200' },
        { label: 'Offline', count: counts.offline, icon: WifiOff, color: 'text-content-tertiary', bg: 'bg-surface-muted border-surface-border' },
      ].map(({ label, count, icon: Icon, color, bg }) => (
        <div key={label} className={`rounded-xl border ${bg} p-4 flex items-center gap-3`}>
          <Icon size={20} className={color} />
          <div>
            <p className="text-2xl font-bold text-content-primary" style={{ fontFamily: 'var(--font-display)' }}>{count}</p>
            <p className="text-xs text-content-secondary">{label}</p>
          </div>
        </div>
      ))}
    </div>
  )
}
