import type { CompanyJSON } from '../types/companyData'
import c1Data from '../../docs/data_output.json'

export async function fetchCompanyData(companyId: 'C1' | 'C2' | 'C3'): Promise<CompanyJSON | null> {
  if (import.meta.env.VITE_USE_MOCK === 'true') {
    if (companyId === 'C1') return Promise.resolve(c1Data as CompanyJSON)
    return Promise.resolve(null)
  }
  const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}/company/${companyId}/data`)
  if (!res.ok) throw new Error(`Company data fetch failed: ${res.status}`)
  if (!res.headers.get('content-type')?.includes('application/json')) {
    throw new Error('Company data fetch returned non-JSON response — is VITE_API_BASE_URL set correctly?')
  }
  return res.json() as Promise<CompanyJSON>
}
