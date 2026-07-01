import { useEffect } from 'react'
import type { RefObject } from 'react'

export function useClickOutside<T extends HTMLElement>(ref: RefObject<T | null>, handler: () => void) {
  useEffect(() => {
    function listener(e: MouseEvent) {
      if (ref.current && !ref.current.contains(e.target as Node)) {
        handler()
      }
    }
    document.addEventListener('mousedown', listener)
    return () => document.removeEventListener('mousedown', listener)
  }, [ref, handler])
}
