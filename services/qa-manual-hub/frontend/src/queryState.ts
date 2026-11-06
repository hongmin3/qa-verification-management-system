import { useMemo } from 'react'
import { useSearchParams } from 'react-router-dom'
import { compareSortValues, isEmptySortValue, type SortColumn, type SortState } from './components/ui'

type Filters = Record<string, string | boolean>

export function readFilters<T extends Filters>(params: URLSearchParams, defaults: T): T {
  const result = { ...defaults }
  for (const key of Object.keys(defaults) as (keyof T & string)[]) {
    const value = params.get(key)
    if (value !== null) {
      result[key] = (typeof defaults[key] === 'boolean' ? value === 'true' : value) as T[typeof key]
    }
  }
  return result
}

export function writeFilters<T extends Filters>(params: URLSearchParams, values: T, defaults: T) {
  const next = new URLSearchParams(params)
  for (const key of Object.keys(defaults)) {
    if (values[key] === defaults[key]) next.delete(key)
    else next.set(key, String(values[key]))
  }
  return next
}

/** URL is the sort state, including after browser Back/Forward navigation. */
export function useQuerySort<T, K extends string>(rows: T[] | null, columns: readonly SortColumn<T, K>[]) {
  const [params, setParams] = useSearchParams()
  const key = columns.find((column) => column.key === params.get('sort'))?.key ?? columns[0].key
  const dir = params.get('dir') === 'desc' ? 'desc' : 'asc'
  const sort: SortState<K> = { key, dir }
  const sorted = useMemo(() => {
    if (!rows) return rows
    const column = columns.find((item) => item.key === key)!
    return [...rows].sort((a, b) => {
      const av = column.value(a)
      const bv = column.value(b)
      const aEmpty = isEmptySortValue(av)
      const bEmpty = isEmptySortValue(bv)
      if (aEmpty !== bEmpty) return aEmpty ? 1 : -1
      const primary = aEmpty ? 0 : compareSortValues(av, bv) * (dir === 'asc' ? 1 : -1)
      return primary || compareSortValues(columns[0].value(a), columns[0].value(b))
    })
  }, [rows, columns, key, dir])
  const toggle = (selected: K) => setParams((previous) => {
    const next = new URLSearchParams(previous)
    next.set('sort', selected)
    const direction = selected === key && dir === 'asc' ? 'desc' : 'asc'
    if (direction === 'asc') next.delete('dir')
    else next.set('dir', direction)
    return next
  })
  return { sort, toggle, sorted }
}
