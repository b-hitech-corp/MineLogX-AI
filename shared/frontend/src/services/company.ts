import type { CompanyJSON } from '../types/companyData'
import { apiFetch } from './api'

export async function fetchCompanyData(companyId: string): Promise<CompanyJSON> {
  return apiFetch<CompanyJSON>('/analyze', {
    method: 'POST',
    body: JSON.stringify({ company: companyId.toLowerCase() }),
  })
}
