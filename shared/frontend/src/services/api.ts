export async function apiFetch<T>(endpoint: string, options?: RequestInit): Promise<T> {
  const res = await fetch(`${import.meta.env.VITE_API_BASE_URL}${endpoint}`, {
    headers: { 'Content-Type': 'application/json' },
    ...options,
  })
  if (!res.ok) throw new Error(`API error: ${res.status}`)
  if (!res.headers.get('content-type')?.includes('application/json')) {
    throw new Error(`API returned non-JSON response for ${endpoint} — is VITE_API_BASE_URL set correctly?`)
  }
  return res.json() as Promise<T>
}
