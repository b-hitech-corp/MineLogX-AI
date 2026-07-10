import { StatusPill } from '../../ui/StatusPill'
import type { GPSAsset, PitZone } from '../../../types/gps'

export const MAP_HEIGHT = 520
// API returns x/y/width/height normalized to a 0-100 scale, independent of the pixel container size
const MAP_VIEWBOX_SIZE = 100

const zoneColors: Record<string, { fill: string; stroke: string; text: string }> = {
  pit: { fill: '#1B6FEB40', stroke: '#1B6FEB', text: '#5B9AF5' },
  dump: { fill: '#1D9E7540', stroke: '#1D9E75', text: '#3DD68C' },
  workshop: { fill: '#8B949E40', stroke: '#8B949E', text: '#C3CAD4' },
  'fuel-bay': { fill: '#EF9F2740', stroke: '#EF9F27', text: '#F5B94F' },
  'haul-road': { fill: '#A371F740', stroke: '#A371F7', text: '#BC96FA' },
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
        <svg
          width="100%"
          height="100%"
          viewBox={`0 0 ${MAP_VIEWBOX_SIZE} ${MAP_VIEWBOX_SIZE}`}
          preserveAspectRatio="none"
          className="absolute inset-0"
        >
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
                  strokeWidth={0.4}
                  rx={1.2}
                />
                <text
                  x={z.x + z.width / 2}
                  y={z.y + z.height / 2}
                  textAnchor="middle"
                  dominantBaseline="middle"
                  fill={cfg.text}
                  fontSize={2}
                  fontWeight={600}
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
                opacity={isSelected ? 1 : 0.3}
                onClick={() => onSelectAsset(isSelected ? null : asset.id)}
              >
                {isSelected && (
                  <circle r={3.8} fill="none" stroke="#1B6FEB" strokeWidth={0.4} strokeDasharray="0.8 0.4" opacity={0.8} />
                )}
                <circle
                  r={2.7}
                  fill={isIdle ? '#EF9F2720' : '#1B6FEB20'}
                  stroke={isIdle ? '#EF9F27' : '#1B6FEB'}
                  strokeWidth={isSelected ? 0.4 : 0.3}
                />
                <text textAnchor="middle" dominantBaseline="middle" fontSize={2.7}>
                  {assetIcons[asset.assetType] ?? '⚙️'}
                </text>
              </g>
            )
          })}
        </svg>

        {zones.length > 0 && (
          <div className="absolute bottom-3 right-3 rounded-lg border border-surface-border bg-surface-card/90 px-3 py-2 backdrop-blur-sm">
            <p className="mb-1.5 text-[10px] font-semibold uppercase tracking-wide text-content-tertiary">Zones</p>
            <div className="flex flex-col gap-1">
              {zones.map((z) => (
                <div key={z.id} className="flex items-center gap-2">
                  <span
                    className="h-2.5 w-2.5 shrink-0 rounded-sm"
                    style={{ backgroundColor: zoneColors[z.type].stroke }}
                  />
                  <span className="text-xs text-content-secondary">{z.name}</span>
                </div>
              ))}
            </div>
          </div>
        )}
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
