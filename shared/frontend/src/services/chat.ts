import { apiFetch } from './api'

export async function sendChatPrompt(query: string, model: string, client: string): Promise<string> {
  console.log(`Sending chat prompt: ${JSON.stringify({ query, model, client })}`)
  const data = await apiFetch<unknown>('/chat', {
    method: 'POST',
    body: JSON.stringify({ query, model, client }),
  })
  if (typeof data === 'string') return data
  if (typeof (data as { answer?: unknown })?.answer === 'string') return (data as { answer: string }).answer
  if (typeof (data as { response?: unknown })?.response === 'string') return (data as { response: string }).response
  if (typeof (data as { message?: unknown })?.message === 'string') return (data as { message: string }).message
  return JSON.stringify(data)
}
