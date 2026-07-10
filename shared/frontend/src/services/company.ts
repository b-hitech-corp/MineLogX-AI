import type { CompanyJSON } from '../types/companyData'
import { apiFetch } from './api'

const COMPANY_ENDPOINT = import.meta.env.VITE_COMPANY_ENDPOINT ?? (
  import.meta.env.DEV
    ? '/company-proxy/'
    : 'https://5ke5e7f2ofyxonkh62groo7s7i0zunmq.lambda-url.us-east-1.on.aws/'
)

export async function fetchCompanyData(companyId: string): Promise<CompanyJSON> {
  return apiFetch<CompanyJSON>(COMPANY_ENDPOINT, {
    baseUrl: '',
    requireJsonContentType: false,
    method: 'POST',
    body: JSON.stringify({ company: companyId.toLowerCase() }),
  })
}
