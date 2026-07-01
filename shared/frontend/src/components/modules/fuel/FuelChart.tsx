import { LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer } from 'recharts'

interface DataPoint {
  hour: string
  consumption: number
}

export function FuelChart({ data }: { data: DataPoint[] }) {
  return (
    <div className="rounded-xl border border-surface-border bg-surface-card p-4">
      <h3 className="mb-4 text-sm font-semibold text-content-primary">Truck 204 — Fuel Consumption Trend (L/h)</h3>
      <ResponsiveContainer width="100%" height={200}>
        <LineChart data={data} margin={{ top: 4, right: 8, left: -20, bottom: 0 }}>
          <CartesianGrid strokeDasharray="3 3" stroke="#21262D" />
          <XAxis dataKey="hour" tick={{ fill: '#484F58', fontSize: 11 }} axisLine={false} tickLine={false} />
          <YAxis tick={{ fill: '#484F58', fontSize: 11 }} axisLine={false} tickLine={false} domain={['auto', 'auto']} />
          <Tooltip
            contentStyle={{ background: '#161B22', border: '1px solid #21262D', borderRadius: 8, color: '#F0F6FC' }}
            itemStyle={{ color: '#EF9F27' }}
            labelStyle={{ color: '#8B949E' }}
          />
          <Line
            type="monotone"
            dataKey="consumption"
            stroke="#EF9F27"
            strokeWidth={2}
            dot={{ fill: '#EF9F27', strokeWidth: 0, r: 3 }}
            name="L/h"
          />
        </LineChart>
      </ResponsiveContainer>
    </div>
  )
}
