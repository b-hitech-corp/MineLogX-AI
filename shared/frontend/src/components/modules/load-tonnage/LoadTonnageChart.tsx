import { AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer, ReferenceLine } from 'recharts'

interface DataPoint {
  time: string
  tonnes: number
}

interface LoadTonnageChartProps {
  data: DataPoint[]
  target: number
}

export function LoadTonnageChart({ data, target }: LoadTonnageChartProps) {
  return (
    <div className="rounded-xl border border-surface-border bg-surface-card p-4">
      <h3 className="mb-4 text-sm font-semibold text-content-primary">Cumulative Tonnes — Day Shift</h3>
      <ResponsiveContainer width="100%" height={220}>
        <AreaChart data={data} margin={{ top: 4, right: 8, left: -10, bottom: 0 }}>
          <defs>
            <linearGradient id="tonnageGrad" x1="0" y1="0" x2="0" y2="1">
              <stop offset="5%" stopColor="#1B6FEB" stopOpacity={0.3} />
              <stop offset="95%" stopColor="#1B6FEB" stopOpacity={0.02} />
            </linearGradient>
          </defs>
          <CartesianGrid strokeDasharray="3 3" stroke="#21262D" />
          <XAxis dataKey="time" tick={{ fill: '#484F58', fontSize: 11 }} axisLine={false} tickLine={false} />
          <YAxis tick={{ fill: '#484F58', fontSize: 11 }} axisLine={false} tickLine={false} tickFormatter={(v) => `${(v / 1000).toFixed(0)}k`} />
          <Tooltip
            contentStyle={{ background: '#161B22', border: '1px solid #21262D', borderRadius: 8, color: '#F0F6FC' }}
            itemStyle={{ color: '#1B6FEB' }}
            labelStyle={{ color: '#8B949E' }}
            formatter={(v: number) => [`${v.toLocaleString()} t`, 'Tonnes']}
          />
          <ReferenceLine y={target} stroke="#EF9F27" strokeDasharray="4 2" label={{ value: 'Target', fill: '#EF9F27', fontSize: 10, position: 'insideTopRight' }} />
          <Area type="monotone" dataKey="tonnes" stroke="#1B6FEB" strokeWidth={2} fill="url(#tonnageGrad)" name="Tonnes" />
        </AreaChart>
      </ResponsiveContainer>
    </div>
  )
}
