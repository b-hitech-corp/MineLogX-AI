interface ApiFetchOptions extends RequestInit {
  baseUrl?: string
  requireJsonContentType?: boolean
}

export async function apiFetch<T>(endpoint: string, options?: ApiFetchOptions): Promise<T> {
  const { baseUrl = import.meta.env.VITE_API_BASE_URL, requireJsonContentType = true, ...init } = options ?? {}
  const res = await fetch(`${baseUrl}${endpoint}`, {
    ...init,
    headers: {
      ...(init.body ? { 'Content-Type': 'application/json' } : {}),
      ...init.headers,
    },
  })
  if (!res.ok) throw new Error(`API error: ${res.status}`)
  if (requireJsonContentType && !res.headers.get('content-type')?.includes('application/json')) {
    throw new Error(`API returned non-JSON response for ${endpoint} — is VITE_API_BASE_URL set correctly?`)
  }
  return res.json() as Promise<T>
}
