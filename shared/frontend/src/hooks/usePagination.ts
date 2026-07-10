import { useEffect, useState } from 'react'

interface UsePaginationResult<T> {
  page: number
  setPage: (page: number) => void
  totalPages: number
  pageItems: T[]
}

export function usePagination<T>(items: T[], pageSize = 10): UsePaginationResult<T> {
  const [page, setPage] = useState(1)
  const totalPages = Math.max(1, Math.ceil(items.length / pageSize))

  useEffect(() => {
    if (page > totalPages) setPage(1)
  }, [totalPages, page])

  const pageItems = items.slice((page - 1) * pageSize, page * pageSize)

  return { page, setPage, totalPages, pageItems }
}
