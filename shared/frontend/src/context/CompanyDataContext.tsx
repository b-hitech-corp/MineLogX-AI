import { createContext, useContext, useEffect, useRef, useState } from 'react'
import type { ReactNode } from 'react'
import type { CompanyJSON } from '../types/companyData'
import { fetchCompanyData } from '../services/company'
import { useApp } from './AppContext'

interface CompanyDataContextType {
  data: CompanyJSON | null
  isLoading: boolean
  error: string | null
}

const CompanyDataContext = createContext<CompanyDataContextType | undefined>(undefined)

export function CompanyDataProvider({ children }: { children: ReactNode }) {
  const { selectedCompany } = useApp()
  const cacheRef = useRef<Map<string, CompanyJSON>>(new Map())
  const [data, setData] = useState<CompanyJSON | null>(null)
  const [isLoading, setIsLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    const cached = cacheRef.current.get(selectedCompany.id)
    if (cached) {
      setData(cached)
      setError(null)
      setIsLoading(false)
      return
    }

    let cancelled = false
    setData(null)
    setError(null)
    setIsLoading(true)
    fetchCompanyData(selectedCompany.id)
      .then((result) => {
        if (cancelled) return
        cacheRef.current.set(selectedCompany.id, result)
        setData(result)
        setIsLoading(false)
      })
      .catch((e: unknown) => { if (!cancelled) { setError(String(e)); setIsLoading(false) } })
    return () => { cancelled = true }
  }, [selectedCompany.id])

  return (
    <CompanyDataContext.Provider value={{ data, isLoading, error }}>
      {children}
    </CompanyDataContext.Provider>
  )
}

export function useCompanyData() {
  const ctx = useContext(CompanyDataContext)
  if (!ctx) throw new Error('useCompanyData must be used within CompanyDataProvider')
  return ctx
}
