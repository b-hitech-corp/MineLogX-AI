import { MessageSquare } from 'lucide-react'
import { useChat } from '../../../context/ChatContext'
import { useAlerts } from '../../../context/AlertsContext'

export function ChatFAB() {
  const { openChat, isOpen } = useChat()
  const { criticalCount } = useAlerts()

  return (
    <button
      onClick={openChat}
      aria-label="Open AI assistant"
      className={`fixed bottom-6 right-6 z-40 flex h-14 w-14 items-center justify-center rounded-full bg-brand-blue shadow-lg transition-all duration-200 cursor-pointer hover:bg-blue-600 hover:scale-105 active:scale-95 ${
        isOpen ? 'pointer-events-none scale-75 opacity-0' : 'scale-100 opacity-100'
      }`}
    >
      <MessageSquare size={22} className="text-white" />
      {criticalCount > 0 && (
        <span
          className={`absolute -right-1 -top-1 flex h-5 w-5 items-center justify-center rounded-full bg-status-critical text-[10px] font-bold text-white transition-opacity duration-150 ${
            isOpen ? 'opacity-0' : 'opacity-100'
          }`}
        >
          {criticalCount}
        </span>
      )}
    </button>
  )
}
