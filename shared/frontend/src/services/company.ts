import type { CompanyJSON } from '../types/companyData'
import { apiFetch } from './api'

export async function fetchCompanyData(companyId: string): Promise<CompanyJSON> {
  return apiFetch<CompanyJSON>('/analyze', {
    method: 'POST',
    body: JSON.stringify({ company: companyId.toLowerCase() }),
    baseUrl: import.meta.env.VITE_ANALYZE_URL || import.meta.env.VITE_API_BASE_URL,
  })
}
