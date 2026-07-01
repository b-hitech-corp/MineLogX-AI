import { useEffect, useState } from 'react'
import { PageHeader } from '../components/layout/PageHeader'
import { FleetStatusBar } from '../components/modules/fleet/FleetStatusBar'
import { FleetTable } from '../components/modules/fleet/FleetTable'
import { getFleetAssets } from '../services/fleet'
import { SectionDataLoader } from '../components/ui/SectionDataLoader'
import { JsonSectionBlock } from '../components/modules/shared/JsonSectionBlock'
import { useCompanyData } from '../context/CompanyDataContext'
import type { FleetAsset, FleetStatus } from '../types/fleet'

const ALL_STATUSES: FleetStatus[] = ['active', 'idle', 'maintenance', 'offline']

export function FleetPage() {
  const [assets, setAssets] = useState<FleetAsset[]>([])
  const [filter, setFilter] = useState<FleetStatus | 'all'>('all')
  const { data } = useCompanyData()

  useEffect(() => {
    getFleetAssets().then(setAssets)
  }, [])

  const filtered = filter === 'all' ? assets : assets.filter((a) => a.status === filter)

  return (
    <div className="flex flex-col gap-4 sm:gap-6">
      <PageHeader
        title="Fleet Management"
        subtitle={`${assets.length} assets · Day Shift`}
      />
      <div className="grid grid-cols-1 gap-4 sm:gap-6 xl:grid-cols-2">
        <FleetStatusBar assets={assets} />
        <div className="flex flex-wrap items-center gap-2 content-start">
          {(['all', ...ALL_STATUSES] as const).map((s) => (
            <button
              key={s}
              onClick={() => setFilter(s)}
              className={`rounded-lg px-3 py-1.5 text-xs font-medium transition-colors cursor-pointer capitalize ${
                filter === s
                  ? 'bg-brand-blue-dim text-brand-blue'
                  : 'bg-glass backdrop-blur-md border border-glass-border text-content-secondary hover:text-content-primary'
              }`}
            >
              {s === 'all' ? 'All Assets' : s}
            </button>
          ))}
        </div>
      </div>

      <FleetTable assets={filtered} />

      <SectionDataLoader>
        {data && <JsonSectionBlock section={data.fleet} title="Fleet Analytics" />}
      </SectionDataLoader>
    </div>
  )
}
