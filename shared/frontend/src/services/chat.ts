const CHAT_ENDPOINT =
  import.meta.env.VITE_CHAT_ENDPOINT ??
  (import.meta.env.DEV
    ? '/chat-proxy/'
    : 'https://szfoqv25uftblx6xpowrslzi3y0yumcy.lambda-url.us-east-1.on.aws/')

export async function sendChatPrompt(query: string, model?: string): Promise<string> {
  const res = await fetch(CHAT_ENDPOINT, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ query, model }),
  })
  if (!res.ok) throw new Error(`Chat API error: ${res.status}`)
  const data = await res.json()
  if (typeof data === 'string') return data
  if (typeof data?.answer === 'string') return data.answer
  if (typeof data?.response === 'string') return data.response
  if (typeof data?.message === 'string') return data.message
  return JSON.stringify(data)
}
