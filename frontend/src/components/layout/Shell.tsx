import type { ReactNode } from 'react'
import { Header } from './Header'

export function Shell({ children }: { children: ReactNode }) {
  return (
    <div className="flex h-screen flex-col overflow-hidden">
      <Header />
      <main className="flex-1 overflow-y-auto p-4 pb-5 sm:p-6 sm:pb-8 xl:p-8 xl:pb-10">
        {children}
      </main>
    </div>
  )
}
