import type { CompanyJSON } from '../types/companyData'

const COMPANY_ENDPOINT = import.meta.env.VITE_COMPANY_ENDPOINT ?? (
  import.meta.env.DEV
    ? '/company-proxy/'
    : 'https://5ke5e7f2ofyxonkh62groo7s7i0zunmq.lambda-url.us-east-1.on.aws/'
)

export async function fetchCompanyData(companyId: string): Promise<CompanyJSON> {
  const res = await fetch(COMPANY_ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ company: companyId.toLowerCase() }),
  })
  if (!res.ok) throw new Error(`Company data fetch failed: ${res.status}`)
  return res.json() as Promise<CompanyJSON>
}
