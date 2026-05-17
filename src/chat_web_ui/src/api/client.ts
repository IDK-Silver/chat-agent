const BASE = ''

export async function fetchDashboard(from: string, to: string) {
  const res = await fetch(`${BASE}/api/dashboard?from=${from}&to=${to}`)
  return res.json()
}

export async function fetchSessions(from: string, to: string, limit = 20, offset = 0) {
  const res = await fetch(`${BASE}/api/sessions?from=${from}&to=${to}&limit=${limit}&offset=${offset}`)
  return res.json()
}

export async function fetchSessionDetail(id: string) {
  const res = await fetch(`${BASE}/api/sessions/${id}`)
  return res.json()
}

export async function fetchAllRequests(
  from: string,
  to: string,
  limit = 200,
  offset = 0,
  clientLabel?: string,
) {
  const params = new URLSearchParams({
    from,
    to,
    limit: String(limit),
    offset: String(offset),
  })
  if (clientLabel) params.set('client_label', clientLabel)
  const res = await fetch(`${BASE}/api/requests?${params.toString()}`)
  return res.json()
}

export async function fetchRequestDetail(sessionId: string, requestId: string) {
  const res = await fetch(
    `${BASE}/api/sessions/${encodeURIComponent(sessionId)}/requests/${encodeURIComponent(requestId)}`,
  )
  return res.json()
}

export async function fetchLiveStatus() {
  const res = await fetch(`${BASE}/api/live`)
  return res.json()
}
