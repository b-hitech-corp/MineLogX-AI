import { MessageSquare } from 'lucide-react'
import { useChat } from '../../../context/ChatContext'
import { useAlerts } from '../../../context/AlertsContext'

export function ChatFAB() {
  const { openChat, isOpen } = useChat()
  const { criticalCount } = useAlerts()

  if (isOpen) return null

  return (
    <button
      onClick={openChat}
      className="fixed bottom-6 right-6 z-40 flex h-14 w-14 items-center justify-center rounded-full bg-brand-blue shadow-lg hover:bg-blue-600 transition-colors cursor-pointer"
    >
      <MessageSquare size={22} className="text-white" />
      {criticalCount > 0 && (
        <span className="absolute -top-1 -right-1 flex h-5 w-5 items-center justify-center rounded-full bg-status-critical text-[10px] font-bold text-white">
          {criticalCount}
        </span>
      )}
    </button>
  )
}
