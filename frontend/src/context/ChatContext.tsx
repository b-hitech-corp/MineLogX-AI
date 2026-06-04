import { createContext, useContext, useState } from 'react'
import type { ReactNode } from 'react'
import type { ChatMessage } from '../types/chat'
import { mockInitialMessages, demoResponses } from '../mocks/chat'
import { sendChatPrompt } from '../services/chat'

// VITE_CHAT_MOCK overrides the global VITE_USE_MOCK for the chat module
const USE_MOCK =
  import.meta.env.VITE_CHAT_MOCK !== undefined
    ? import.meta.env.VITE_CHAT_MOCK === 'true'
    : import.meta.env.VITE_USE_MOCK === 'true'

interface ChatContextType {
  isOpen: boolean
  openChat: () => void
  closeChat: () => void
  messages: ChatMessage[]
  sendMessage: (content: string) => void
  isTyping: boolean
}

const ChatContext = createContext<ChatContextType | undefined>(undefined)

export function ChatProvider({ children }: { children: ReactNode }) {
  const [isOpen, setIsOpen] = useState(false)
  const [messages, setMessages] = useState<ChatMessage[]>(mockInitialMessages)
  const [isTyping, setIsTyping] = useState(false)

  async function sendMessage(content: string) {
    const userMsg: ChatMessage = {
      id: `msg-${Date.now()}`,
      role: 'user',
      content,
      timestamp: new Date().toISOString(),
    }
    setMessages((prev) => [...prev, userMsg])
    setIsTyping(true)

    if (USE_MOCK) {
      setTimeout(() => {
        const lower = content.toLowerCase()
        const match = demoResponses.find((r) => r.keywords.some((k) => lower.includes(k)))
        const response: ChatMessage = match
          ? { ...match.response, id: `resp-${Date.now()}`, timestamp: new Date().toISOString() }
          : {
              id: `resp-${Date.now()}`,
              role: 'assistant',
              content:
                'I can help you with fleet status, fuel anomalies, maintenance scheduling, KPI analysis, safety events, and more. Try asking about "Truck 204", "alerts", "fuel", or "fleet status".',
              timestamp: new Date().toISOString(),
            }
        setMessages((prev) => [...prev, response])
        setIsTyping(false)
      }, 1200)
      return
    }

    try {
      const text = await sendChatPrompt(content)
      setMessages((prev) => [
        ...prev,
        { id: `resp-${Date.now()}`, role: 'assistant', content: text, timestamp: new Date().toISOString() },
      ])
    } catch {
      setMessages((prev) => [
        ...prev,
        {
          id: `resp-${Date.now()}`,
          role: 'assistant',
          content: 'Sorry, I could not reach the AI service. Please try again.',
          timestamp: new Date().toISOString(),
        },
      ])
    } finally {
      setIsTyping(false)
    }
  }

  return (
    <ChatContext.Provider
      value={{
        isOpen,
        openChat: () => setIsOpen(true),
        closeChat: () => setIsOpen(false),
        messages,
        sendMessage,
        isTyping,
      }}
    >
      {children}
    </ChatContext.Provider>
  )
}

export function useChat() {
  const ctx = useContext(ChatContext)
  if (!ctx) throw new Error('useChat must be used within ChatProvider')
  return ctx
}
