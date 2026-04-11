export function formatCost(cost: number | null | undefined): string {
  if (cost == null) return '-'
  return `$${cost.toFixed(4)}`
}

export function formatCostShort(cost: number | null | undefined): string {
  if (cost == null) return '-'
  if (cost < 0.01) return `$${cost.toFixed(4)}`
  return `$${cost.toFixed(2)}`
}

export function formatTokens(tokens: number): string {
  if (tokens >= 1_000_000) return `${(tokens / 1_000_000).toFixed(1)}M`
  if (tokens >= 1_000) return `${(tokens / 1_000).toFixed(0)}k`
  return tokens.toLocaleString()
}

export function formatPercent(value: number | null | undefined): string {
  if (value == null) return '-'
  return `${(value * 100).toFixed(1)}%`
}

export function formatCacheRate(value: number | null | undefined): string {
  if (value == null) return '無法測量'
  return `${(value * 100).toFixed(1)}%`
}

export function formatCacheWriteTokens(
  measurable: boolean | null | undefined,
  tokens: number | null | undefined,
): string {
  if (!measurable) return '無法測量'
  return formatTokens(tokens ?? 0)
}

export function formatLatency(ms: number): string {
  if (ms >= 1000) return `${(ms / 1000).toFixed(1)}s`
  return `${ms}ms`
}
