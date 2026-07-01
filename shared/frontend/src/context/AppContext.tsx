import { createContext, useContext, useState } from 'react'
import type { ReactNode } from 'react'

export type ActiveModule =
  | 'overview'
  | 'fleet'
  | 'maintenance'
  | 'kpis'
  | 'load-tonnage'
  | 'fuel'
  | 'gps'
  | 'safety'

export interface Company {
  id: 'C1' | 'C2' | 'C3'
  name: string
}

export const COMPANIES: Company[] = [
  { id: 'C1', name: 'Apex Mining Co.' },
  { id: 'C2', name: 'Ridgeline Resources' },
  { id: 'C3', name: 'Ironstone Group' },
]

interface AppContextType {
  activeModule: ActiveModule
  setActiveModule: (module: ActiveModule) => void
  currentShift: string
  currentUser: string
  selectedCompany: Company
  selectCompany: (company: Company) => void
}

const AppContext = createContext<AppContextType | undefined>(undefined)

export function AppProvider({ children }: { children: ReactNode }) {
  const [activeModule, setActiveModule] = useState<ActiveModule>('overview')
  const [selectedCompany, setSelectedCompany] = useState<Company>(COMPANIES[0])

  function selectCompany(company: Company) {
    setSelectedCompany(company)
  }

  return (
    <AppContext.Provider
      value={{
        activeModule,
        setActiveModule,
        currentShift: 'Day Shift — 06:00–18:00',
        currentUser: 'User',
        selectedCompany,
        selectCompany,
      }}
    >
      {children}
    </AppContext.Provider>
  )
}

export function useApp() {
  const ctx = useContext(AppContext)
  if (!ctx) throw new Error('useApp must be used within AppProvider')
  return ctx
}
