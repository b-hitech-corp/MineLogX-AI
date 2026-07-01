# MineLogX — Claude Project Instructions

## Project Overview
MineLogX is a mining operational intelligence platform. It combines real-time operational dashboards, KPI reporting, anomaly detection, and an AI-powered chat assistant into a single unified experience. The goal is to help mining operators answer questions like: what is happening right now, which assets need attention, and what actions should be taken next.

## Tech Stack
- **Framework**: React 19 + Vite
- **Styling**: Tailwind CSS v4
- **State management**: Context API
- **Data fetching**: Native `fetch` (no axios or react-query)
- **Package manager**: pnpm
- **Language**: TypeScript

## Project Structure
```
src/
├── assets/               # Static assets (icons, images)
├── components/
│   ├── ui/               # Reusable low-level components (Button, Badge, Card, etc.)
│   ├── layout/           # Shell, Sidebar, Topbar, PageHeader
│   └── modules/          # Feature-specific components per module
│       ├── overview/
│       ├── fleet/
│       ├── maintenance/
│       ├── fuel/
│       ├── gps/
│       ├── kpis/
│       ├── load-tonnage/
│       ├── safety/
│       ├── compliance/
│       └── chat/
├── context/              # All Context API providers
│   ├── AppContext.tsx     # Global app state (active module, user, shift)
│   ├── AlertsContext.tsx  # Active alerts and anomalies
│   └── ChatContext.tsx    # Chat open/close state and message history
├── hooks/                # Custom hooks (useFetch, useAlerts, useKPIs, etc.)
├── services/             # All fetch calls organized by domain
│   ├── api.ts            # Base fetch wrapper with error handling
│   ├── fleet.ts
│   ├── kpis.ts
│   ├── maintenance.ts
│   ├── fuel.ts
│   └── telemetry.ts
├── types/                # TypeScript interfaces and types
│   ├── fleet.ts
│   ├── kpis.ts
│   ├── alerts.ts
│   └── chat.ts
├── utils/                # Helpers (formatters, unit converters, classnames)
├── pages/                # Top-level page components (one per sidebar module)
├── App.tsx
└── main.tsx
```

## Design System

### Brand Direction
The platform must feel like a cohesive MineLogX product — not a generic dashboard. The visual identity is:
- **Dark, professional, and data-dense** — inspired by operational control rooms
- **MineLogX blue as the accent** — highlights, active states, and primary actions
- **Navy/black primary backgrounds** — sidebar, topbar, and main surface
- **White and light gray for text and supporting elements** on dark backgrounds
- **Red, amber, and green strictly reserved** for alerts, warnings, and status indicators — never used decoratively

The logo is provided separately as an asset. Always use it in the topbar — do not recreate or substitute it.

### Color Palette
Define these as custom Tailwind tokens in `tailwind.config.ts`:

```ts
colors: {
  brand: {
    blue:       '#1B6FEB', // MineLogX blue — primary actions, active states, highlights
    'blue-dim': '#1A3A6B', // Muted blue — hover states, secondary highlights
  },
  surface: {
    DEFAULT: '#0D1117', // Main background
    card:    '#161B22', // Card and panel background
    border:  '#21262D', // Borders and dividers
    muted:   '#30363D', // Subtle borders, disabled states
  },
  content: {
    primary:   '#F0F6FC', // Primary text on dark backgrounds
    secondary: '#8B949E', // Secondary / muted text
    tertiary:  '#484F58', // Placeholder, disabled text
  },
  status: {
    critical: '#E24B4A', // Critical alerts, failures
    warning:  '#EF9F27', // Warnings, below-target
    healthy:  '#1D9E75', // On-target, healthy, resolved
    info:     '#1B6FEB', // Informational
  },
}
```

### Component Conventions
- All reusable UI components live in `src/components/ui/`
- Use `cn()` utility (clsx + tailwind-merge) for conditional class composition
- Cards use `rounded-xl border border-surface-border bg-surface-card p-4` as base
- KPI cards always show: label, value, trend indicator, and a progress bar
- Status pills: `rounded-full px-2 py-0.5 text-xs font-medium`
- Text on dark backgrounds: primary content uses `text-content-primary`, labels use `text-content-secondary`

### Status Color Mapping
```ts
const statusColors = {
  critical: 'bg-red-900/40 text-red-400 border border-red-800',
  warning:  'bg-amber-900/40 text-amber-400 border border-amber-800',
  healthy:  'bg-green-900/40 text-green-400 border border-green-800',
  inactive: 'bg-surface-muted text-content-secondary',
  info:     'bg-blue-900/40 text-blue-400 border border-blue-800',
}
```

## KPI Categories
The KPI screen organizes metrics into 5 categories. Always respect this grouping:

1. **Fleet Performance** — Tonnes moved, Tonnes/truck, Completed cycles, Avg haul cycle time
2. **Asset Health & Maintenance** — Equipment availability, Fleet utilization, Maintenance compliance
3. **Operational Efficiency** — Fuel per tonne, Cycle efficiency, Idle time
4. **Sustainability** — Total fuel consumed, Estimated CO₂ emissions
5. **AI Insights & Recommendations** — Equipment health score, Predicted failures, Safety/risk events, AI recommendation card

## Data Fetching Pattern
Use a base `apiFetch` wrapper in `src/services/api.ts`:

```ts
export async function apiFetch<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}${endpoint}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) throw new Error(`API error: ${res.status}`)
  return res.json()
}
```

All service files import and use `apiFetch`. Never call `fetch` directly in components.

For demo/mock data, use a `VITE_USE_MOCK=true` env flag. When true, services return data from `src/mocks/` instead of hitting the API.

## Context API Pattern
Each context follows this structure:

```ts
// 1. Define types
interface AlertsContextType {
  alerts: Alert[]
  criticalCount: number
  dismissAlert: (id: string) => void
}

// 2. Create context with undefined default
const AlertsContext = createContext<AlertsContextType | undefined>(undefined)

// 3. Provider handles state and fetch
export function AlertsProvider({ children }: { children: React.ReactNode }) {
  const [alerts, setAlerts] = useState<Alert[]>([])
  // fetch logic here
  return <AlertsContext.Provider value={{ alerts, criticalCount, dismissAlert }}>{children}</AlertsContext.Provider>
}

// 4. Typed hook
export function useAlerts() {
  const ctx = useContext(AlertsContext)
  if (!ctx) throw new Error('useAlerts must be used within AlertsProvider')
  return ctx
}
```

## AI Insights & Recommendations

AI insights are a **core differentiator** of the platform — not an add-on. Every screen should surface relevant AI-generated context where appropriate.

### Insight Types
```ts
type InsightSeverity = 'info' | 'warning' | 'critical' | 'positive'

interface AIInsight {
  id: string
  severity: InsightSeverity
  module: string          // e.g. 'fleet', 'maintenance', 'fuel'
  asset?: string          // e.g. 'Truck 204', 'EX-12'
  message: string         // Human-readable insight
  recommendation?: string // Suggested action
  timestamp: string
  insightCard?: InsightCardPayload // Optional structured data shown inline
}
```

### Example Insights (use these in mock data)
- "Fuel consumption on Truck 204 increased 18% versus the 7-day average"
- "Predicted maintenance window for Asset EX-12 within 36 operating hours"
- "Haul Route B cycle time improved 9% after operational changes"
- "Fleet utilization below target during current shift"
- "CAT 793-07 engine temperature trending upward for 48 hrs — 72% failure probability within 24 hrs"
- "South Zone haul cycles 18% slower than shift baseline — possible route congestion"

### Placement Rules
- **Overview**: AI insight banner at the top of the page, summarizing current shift
- **KPIs**: Dedicated "AI Insights & Recommendations" category section at the bottom
- **Maintenance**: Predictive failure warnings inline with the asset health table
- **Fleet**: Anomaly flags per asset row
- **Chat panel**: Full conversational AI with structured insight cards in responses

## AI Chat Assistant
- Triggered by a floating button (bottom-right corner) visible on all screens
- Opens as a side panel overlaid on the dashboard (does not replace the view)
- Chat state (open/closed, messages) lives in `ChatContext`
- For demo purposes, chat responses come from mock data or a configured API endpoint
- Each message can optionally include a structured `insightCard` payload rendered inline

## Operational Modules

### Long-term Platform Vision
MineLogX is designed as a **modular operational intelligence platform**. Each module is independently navigable and contributes to a unified operational picture. Future modules include:

- Fleet Management
- Asset Health & Predictive Maintenance
- Safety & Fatigue Management
- Environmental Monitoring
- Load & Tonnage Tracking
- GPS / Pit Navigation
- Compliance & Reporting
- Maximo Integration
- AI Assistant

New modules must be addable without restructuring the core layout or routing. The sidebar is the navigation anchor — adding a module means adding a nav item and a page, nothing more.

### Demo Scope (June)
For the June demo, prioritize these modules with realistic mock data:
- Overview (full)
- Fleet (full)
- Maintenance (full)
- KPIs (full)
- Load / Tonnage (partial)
- Fuel (partial)
- GPS / Location (partial — map with mock asset positions)
- Safety (partial — alert list)

### Demo Success Criteria
The June demo must clearly demonstrate that users can:
1. **Monitor KPIs** — across fleet, maintenance, efficiency, and sustainability categories
2. **Explore operational data** — drill into specific assets, zones, or time ranges
3. **Receive AI-driven insights** — anomaly detection, predictive recommendations, risk flags
4. **Understand platform scalability** — stakeholders should see how MineLogX can grow across mining use cases

## Mock Data
- All mock files live in `src/mocks/`
- Mock data must reflect **realistic mining workflows** — not generic placeholder values
- Each mock module exports a typed array or object matching its corresponding type

### Required mock scenarios (minimum for demo)
- A truck with abnormal fuel consumption (+18% vs 7-day average)
- A delayed haul cycle in South Zone
- An asset with excessive idle time
- A fatigue-related risk indicator
- Tire or equipment health warnings
- GPS / pit movement context for at least 3 assets
- A predictive maintenance recommendation with estimated time-to-failure
- A mock work-order style output linked to an asset alert
- A resolved alert to show the full alert lifecycle

## Environment Variables
```
VITE_API_BASE_URL=http://localhost:3000
VITE_USE_MOCK=true
```

## Commands
```bash
pnpm install         # Install dependencies
pnpm dev             # Start dev server
pnpm build           # Production build
pnpm lint            # ESLint
pnpm type-check      # tsc --noEmit
```

## Code Style
- Functional components only, no class components
- Always type props explicitly — no implicit `any`
- Co-locate component styles with the component (Tailwind classes inline)
- Extract magic numbers and strings into named constants
- Prefer named exports over default exports for components
- Keep components under 150 lines — extract sub-components when they grow
