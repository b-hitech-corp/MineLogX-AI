import { Building2, ChevronDown } from 'lucide-react'
import { useRef, useState } from 'react'
import { useApp, COMPANIES } from '../../context/AppContext'
import { useClickOutside } from '../../hooks/useClickOutside'

const CLOSE_DUR = 150 // matches --dropdown-close-dur

export function CompanySelector() {
  const { selectedCompany, selectCompany } = useApp()
  const [open, setOpen] = useState(false)
  const [isClosing, setIsClosing] = useState(false)
  const ref = useRef<HTMLDivElement>(null)

  function openDropdown() { setOpen(true) }
  function closeDropdown() {
    setIsClosing(true)
    setTimeout(() => { setOpen(false); setIsClosing(false) }, CLOSE_DUR)
  }
  function toggleDropdown() { open ? closeDropdown() : openDropdown() }

  useClickOutside(ref, closeDropdown)

  return (
    <div ref={ref} className="relative">
      <button
        onClick={toggleDropdown}
        className="flex items-center gap-2 rounded-xl border border-glass-border bg-glass px-3 py-1.5 text-xs backdrop-blur-md transition-colors hover:bg-glass-edge cursor-pointer"
      >
        <Building2 size={13} className="text-brand-blue shrink-0" />
        <span className="font-semibold text-content-primary">{selectedCompany.name}</span>
        <ChevronDown
          size={11}
          className={`text-content-tertiary transition-transform duration-150 ${open && !isClosing ? 'rotate-180' : ''}`}
        />
      </button>

      {open && (
        <div
          className={`t-dropdown absolute right-0 top-full z-50 mt-2 min-w-[196px] rounded-2xl border border-glass-border bg-glass-edge py-1 shadow-2xl backdrop-blur-xl ${isClosing ? 'is-closing' : 'is-open'}`}
          style={{ transformOrigin: 'top right' }}
        >
          <p
            className="px-3 pb-1 pt-2 text-[9px] font-semibold tracking-[0.15em] uppercase text-content-tertiary"
            style={{ fontFamily: 'var(--font-mono)' }}
          >
            Select Site
          </p>
          {COMPANIES.map((company) => (
            <button
              key={company.id}
              onClick={() => { selectCompany(company); closeDropdown() }}
              className={`flex w-full items-center justify-between px-3 py-2 text-xs transition-colors cursor-pointer ${
                selectedCompany.id === company.id
                  ? 'bg-brand-blue/10 text-brand-blue font-semibold'
                  : 'text-content-secondary hover:bg-glass hover:text-content-primary'
              }`}
            >
              <span>{company.name}</span>
              {selectedCompany.id === company.id && (
                <span className="h-1.5 w-1.5 rounded-full bg-brand-blue" />
              )}
            </button>
          ))}
        </div>
      )}
    </div>
  )
}
