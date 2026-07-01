import { createContext, useContext, useState, useEffect } from 'react'
import type { ReactNode } from 'react'
import type { Alert } from '../types/alerts'
import { mockAlerts } from '../mocks/alerts'

interface AlertsContextType {
  alerts: Alert[]
  criticalCount: number
  activeCount: number
  dismissAlert: (id: string) => void
}

const AlertsContext = createContext<AlertsContextType | undefined>(undefined)

export function AlertsProvider({ children }: { children: ReactNode }) {
  const [alerts, setAlerts] = useState<Alert[]>([])

  useEffect(() => {
    setAlerts(mockAlerts)
  }, [])

  const activeAlerts = alerts.filter((a) => a.status !== 'resolved')
  const criticalCount = activeAlerts.filter((a) => a.severity === 'critical').length

  function dismissAlert(id: string) {
    setAlerts((prev) =>
      prev.map((a) => (a.id === id ? { ...a, status: 'resolved' as const, resolvedAt: new Date().toISOString() } : a))
    )
  }

  return (
    <AlertsContext.Provider value={{ alerts, criticalCount, activeCount: activeAlerts.length, dismissAlert }}>
      {children}
    </AlertsContext.Provider>
  )
}

export function useAlerts() {
  const ctx = useContext(AlertsContext)
  if (!ctx) throw new Error('useAlerts must be used within AlertsProvider')
  return ctx
}
