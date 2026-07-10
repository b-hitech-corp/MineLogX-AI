import { Search } from 'lucide-react'
import type { InputHTMLAttributes } from 'react'

interface SearchInputProps extends Omit<InputHTMLAttributes<HTMLInputElement>, 'type' | 'value' | 'onChange'> {
  value: string
  onChange: (value: string) => void
}

export function SearchInput({ value, onChange, placeholder, ...props }: SearchInputProps) {
  return (
    <div className="relative">
      <Search size={15} className="absolute left-3 top-1/2 -translate-y-1/2 text-content-tertiary" />
      <input
        type="text"
        value={value}
        onChange={(e) => onChange(e.target.value)}
        placeholder={placeholder}
        className="w-full rounded-lg border border-surface-border bg-surface pl-9 pr-3 py-2 text-sm text-content-primary placeholder:text-content-tertiary focus:outline-none focus:border-brand-blue transition-colors"
        {...props}
      />
    </div>
  )
}
