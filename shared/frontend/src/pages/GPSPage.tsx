import { useEffect, useState } from 'react'
import { PageHeader } from '../components/layout/PageHeader'
import { GPSMap, MAP_HEIGHT } from '../components/modules/gps/GPSMap'
import { StatusPill } from '../components/ui/StatusPill'
import { getGPSAssets, getPitZones } from '../services/telemetry'
import { formatRelativeTime } from '../utils/formatters'
import type { GPSAsset, PitZone } from '../types/gps'
import { SectionDataLoader } from '../components/ui/SectionDataLoader'
import { GpsStatsBlock } from '../components/modules/gps/GpsStatsBlock'
import { useCompanyData } from '../context/CompanyDataContext'

export function GPSPage() {
  const [assets, setAssets] = useState<GPSAsset[]>([])
  const [zones, setZones] = useState<PitZone[]>([])
  const [selected, setSelected] = useState<string | null>(null)
  const { data } = useCompanyData()

  useEffect(() => {
    getGPSAssets().then(setAssets).catch((err) => console.error('Failed to load GPS assets:', err))
    getPitZones().then(setZones).catch((err) => console.error('Failed to load pit zones:', err))
  }, [])

  return (
    <div className="flex flex-col gap-4 sm:gap-6">
      <PageHeader
        title="GPS / Pit Navigation"
        subtitle={`${assets.length} assets tracked · Live positions`}
      />

      <div className="grid grid-cols-1 gap-6 lg:grid-cols-4">
        <div className="col-span-1 lg:col-span-3">
          <GPSMap assets={assets} zones={zones} selectedAsset={selected} onSelectAsset={setSelected} />
        </div>

        <div className="col-span-1 rounded-xl border border-surface-border bg-surface-card p-4">
          <h3 className="mb-3 text-sm font-semibold text-content-primary">Asset List</h3>
          <div className="flex flex-col gap-2 overflow-y-auto" style={{ height: MAP_HEIGHT }}>
            {assets.map((a) => (
              <button
                key={a.id}
                onClick={() => setSelected(selected === a.id ? null : a.id)}
                className={`rounded-2xl border p-3 text-left transition-colors cursor-pointer ${
                  selected === a.id
                    ? 'border-brand-blue bg-brand-blue-dim'
                    : 'border-surface-border bg-surface-card hover:bg-surface-muted'
                }`}
              >
                <div className="flex items-center justify-between mb-1">
                  <span className="text-sm font-medium text-content-primary">{a.assetName}</span>
                  <StatusPill
                    variant={a.status === 'moving' ? 'healthy' : a.status === 'idle' ? 'warning' : 'inactive'}
                    label={a.status}
                  />
                </div>
                <p className="text-xs text-content-secondary">{a.zone}</p>
                <p className="text-xs text-content-tertiary">{a.speed > 0 ? `${a.speed} km/h` : 'Stationary'} · {formatRelativeTime(a.timestamp)}</p>
              </button>
            ))}
          </div>
        </div>
      </div>

      <SectionDataLoader>
        {data && <GpsStatsBlock statistics={data.gps_location.statistics} />}
      </SectionDataLoader>
    </div>
  )
}
