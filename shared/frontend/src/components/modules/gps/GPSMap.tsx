import { StatusPill } from '../../ui/StatusPill'
import type { GPSAsset, PitZone } from '../../../types/gps'

const MAP_WIDTH = 780
const MAP_HEIGHT = 520

const zoneColors: Record<string, { fill: string; stroke: string; label: string }> = {
  pit: { fill: '#1B6FEB15', stroke: '#1B6FEB40', label: 'text-blue-500' },
  dump: { fill: '#1D9E7515', stroke: '#1D9E7540', label: 'text-green-500' },
  workshop: { fill: '#30363D80', stroke: '#484F58', label: 'text-content-secondary' },
  'fuel-bay': { fill: '#EF9F2715', stroke: '#EF9F2740', label: 'text-amber-500' },
  'haul-road': { fill: '#21262D60', stroke: '#30363D', label: 'text-content-tertiary' },
}

const assetIcons: Record<string, string> = {
  'haul-truck': '🚛',
  excavator: '🏗️',
  loader: '🚜',
  dozer: '🚧',
}

interface GPSMapProps {
  assets: GPSAsset[]
  zones: PitZone[]
  selectedAsset: string | null
  onSelectAsset: (id: string | null) => void
}

export function GPSMap({ assets, zones, selectedAsset, onSelectAsset }: GPSMapProps) {
  return (
    <div className="rounded-xl border border-surface-border bg-surface-card p-4">
      <h3 className="mb-3 text-sm font-semibold text-content-primary">Pit Map — Live Asset Positions</h3>
      <div className="relative overflow-hidden rounded-lg bg-surface" style={{ height: MAP_HEIGHT }}>
        <svg width="100%" height="100%" viewBox={`0 0 ${MAP_WIDTH} ${MAP_HEIGHT}`} className="absolute inset-0">
          {zones.map((z) => {
            const cfg = zoneColors[z.type]
            return (
              <g key={z.id}>
                <rect
                  x={z.x}
                  y={z.y}
                  width={z.width}
                  height={z.height}
                  fill={cfg.fill}
                  stroke={cfg.stroke}
                  strokeWidth={1}
                  rx={6}
                />
                <text
                  x={z.x + z.width / 2}
                  y={z.y + z.height / 2}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  fill={cfg.label.includes('blue') ? '#3B82F6' : cfg.label.includes('green') ? '#22C55E' : cfg.label.includes('amber') ? '#F59E0B' : '#484F58'}
                  fontSize={10}
                  fontWeight={500}
                >
                  {z.name}
                </text>
              </g>
            )
          })}

          {assets.map((asset) => {
            const isSelected = selectedAsset === asset.id
            const isIdle = asset.status === 'idle'
            return (
              <g
                key={asset.id}
                transform={`translate(${asset.x}, ${asset.y})`}
                className="cursor-pointer"
                onClick={() => onSelectAsset(isSelected ? null : asset.id)}
              >
                {isSelected && (
                  <circle r={20} fill="none" stroke="#1B6FEB" strokeWidth={2} strokeDasharray="4 2" opacity={0.8} />
                )}
                <circle
                  r={14}
                  fill={isIdle ? '#EF9F2720' : '#1B6FEB20'}
                  stroke={isIdle ? '#EF9F27' : '#1B6FEB'}
                  strokeWidth={isSelected ? 2 : 1.5}
                />
                <text textAnchor="middle" dominantBaseline="middle" fontSize={14}>
                  {assetIcons[asset.assetType] ?? '⚙️'}
                </text>
              </g>
            )
          })}
        </svg>
      </div>

      {selectedAsset && (() => {
        const a = assets.find((x) => x.id === selectedAsset)
        if (!a) return null
        return (
          <div className="mt-3 rounded-lg border border-surface-border p-3 flex items-center gap-4">
            <div className="flex-1">
              <p className="text-sm font-semibold text-content-primary">{a.assetName}</p>
              <p className="text-xs text-content-secondary">{a.zone}</p>
            </div>
            <StatusPill
              variant={a.status === 'moving' ? 'healthy' : a.status === 'idle' ? 'warning' : 'inactive'}
              label={a.status}
            />
            <p className="text-xs text-content-secondary">{a.speed > 0 ? `${a.speed} km/h` : 'Stationary'}</p>
          </div>
        )
      })()}
    </div>
  )
}
