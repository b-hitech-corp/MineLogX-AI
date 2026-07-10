import { AlertTriangle } from 'lucide-react'
import { cn } from '../../../utils/cn'
import { StatusPill } from '../../ui/StatusPill'
import type { FleetAsset } from '../../../types/fleet'

function typeLabel(type: string) {
  const map: Record<string, string> = {
    'haul-truck': 'Haul Truck',
    excavator: 'Excavator',
    loader: 'Loader',
    dozer: 'Dozer',
  }
  return map[type] ?? type
}

export function FleetTable({ assets }: { assets: FleetAsset[] }) {
  return (
    <div className="flex h-full flex-col rounded-xl border border-surface-border bg-surface-card overflow-hidden">
      <div className="flex-1 overflow-auto">
        <table className="w-full text-sm">
          <thead className="sticky top-0 z-10 bg-surface-card">
            <tr className="border-b border-surface-border">
              {['Asset', 'Type', 'Status', 'Location', 'Operator', 'Fuel', 'Speed', 'Cycles', 'Fuel L/h', 'AI Flag'].map(
                (h) => (
                  <th key={h} className="px-4 py-3 text-left text-xs font-medium text-content-tertiary uppercase tracking-wide whitespace-nowrap">
                    {h}
                  </th>
                )
              )}
            </tr>
          </thead>
          <tbody>
            {assets.map((asset) => (
              <tr
                key={asset.id}
                className={cn(
                  'border-b border-surface-border last:border-0 hover:bg-surface-muted/50 transition-colors',
                  asset.anomaly && 'bg-amber-900/5'
                )}
              >
                <td className="px-4 py-3 font-medium text-content-primary whitespace-nowrap">
                  {asset.name}
                </td>
                <td className="px-4 py-3 text-content-secondary whitespace-nowrap">{typeLabel(asset.type)}</td>
                <td className="px-4 py-3">
                  <StatusPill
                    variant={
                      asset.status === 'active'
                        ? 'healthy'
                        : asset.status === 'idle'
                        ? 'warning'
                        : asset.status === 'offline'
                        ? 'inactive'
                        : 'info'
                    }
                    label={asset.status.charAt(0).toUpperCase() + asset.status.slice(1)}
                  />
                </td>
                <td className="px-4 py-3 text-content-secondary max-w-[180px] truncate">{asset.location}</td>
                <td className="px-4 py-3 text-content-secondary whitespace-nowrap">{asset.operator ?? '—'}</td>
                <td className="px-4 py-3 whitespace-nowrap">
                  <div className="flex items-center gap-2">
                    <div className="h-1.5 w-16 rounded-full bg-surface-muted overflow-hidden">
                      <div
                        className={cn(
                          'h-full rounded-full',
                          asset.fuelLevel > 50 ? 'bg-status-healthy' : asset.fuelLevel > 25 ? 'bg-status-warning' : 'bg-status-critical'
                        )}
                        style={{ width: `${asset.fuelLevel}%` }}
                      />
                    </div>
                    <span className="text-xs text-content-secondary">{asset.fuelLevel}%</span>
                  </div>
                </td>
                <td className="px-4 py-3 text-content-secondary whitespace-nowrap">
                  {asset.speedKph > 0 ? `${asset.speedKph} km/h` : '—'}
                </td>
                <td className="px-4 py-3 text-content-secondary">{asset.cyclesCompleted > 0 ? asset.cyclesCompleted : '—'}</td>
                <td className="px-4 py-3 text-content-secondary whitespace-nowrap">
                  {asset.fuelConsumptionLPH > 0 ? `${asset.fuelConsumptionLPH}` : '—'}
                </td>
                <td className="px-4 py-3">
                  {asset.aiAlert ? (
                    <div className="flex items-start gap-1.5 text-amber-400">
                      <AlertTriangle size={13} className="mt-0.5 shrink-0" />
                      <span className="text-xs line-clamp-2 max-w-[220px]">{asset.aiAlert}</span>
                    </div>
                  ) : (
                    <span className="text-xs text-content-tertiary">—</span>
                  )}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </div>
  )
}
