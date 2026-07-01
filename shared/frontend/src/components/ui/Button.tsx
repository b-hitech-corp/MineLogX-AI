import { cn } from '../../utils/cn'
import type { ButtonHTMLAttributes, ReactNode } from 'react'

type ButtonVariant = 'primary' | 'ghost' | 'outline'
type ButtonSize = 'sm' | 'md'

const variants: Record<ButtonVariant, string> = {
  primary: 'bg-brand-blue hover:bg-blue-600 text-white',
  ghost: 'hover:bg-surface-muted text-content-secondary hover:text-content-primary',
  outline: 'border border-surface-border hover:border-surface-muted text-content-secondary hover:text-content-primary',
}

const sizes: Record<ButtonSize, string> = {
  sm: 'px-3 py-1.5 text-xs',
  md: 'px-4 py-2 text-sm',
}

interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant
  size?: ButtonSize
  children: ReactNode
}

export function Button({ variant = 'primary', size = 'md', className, children, ...props }: ButtonProps) {
  return (
    <button
      className={cn(
        'inline-flex items-center gap-2 rounded-lg font-medium transition-colors cursor-pointer',
        variants[variant],
        sizes[size],
        props.disabled && 'opacity-50 cursor-not-allowed',
        className
      )}
      {...props}
    >
      {children}
    </button>
  )
}
