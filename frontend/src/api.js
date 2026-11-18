async function req(path, opts = {}) {
  const r = await fetch('/api' + path, {
    headers: { 'Content-Type': 'application/json', ...(opts.headers || {}) },
    ...opts,
  })
  const text = await r.text()
  let data = null
  try { data = text ? JSON.parse(text) : null } catch { data = text }
  if (!r.ok) {
    const detail = (data && data.detail) || text || r.statusText
    throw new Error(typeof detail === 'string' ? detail : (detail.message || JSON.stringify(detail)))
  }
  return data
}

function q(params) {
  const s = new URLSearchParams()
  for (const [k, v] of Object.entries(params || {})) {
    // Keep '' on purpose: an empty tag is a real bucket, distinct from "no
    // tag filter" (undefined). Only drop truly absent values.
    if (v !== undefined && v !== null) s.set(k, v)
  }
  const out = s.toString()
  return out ? `?${out}` : ''
}

export const api = {
  dashboard: () => req('/dashboard'),
  campaigns: () => req('/campaigns'),
  tags: () => req('/tags'),
  events: (day, tag) => req('/events' + q({ business_day: day, tag })),
  addEvent: (body) => req('/events', { method: 'POST', body: JSON.stringify(body) }),
  facts: (day, tag) => req('/facts' + q({ business_day: day, tag })),
  preview: (day, tag) => req('/redeem/preview' + q({ business_day: day, tag })),
  seal: (body) => req('/redeem/seal', { method: 'POST', body: JSON.stringify(body) }),
  batches: (day) => req('/batches' + q({ business_day: day })),
  getDay: () => req('/settings/day'),
  setDay: (business_day) => req('/settings/day', { method: 'PUT', body: JSON.stringify({ business_day }) }),
  retag: (id, tag) => req(`/events/${id}/tag`, { method: 'POST', body: JSON.stringify({ tag }) }),
  refreshTags: (body) => req('/tags/refresh', { method: 'POST', body: JSON.stringify(body || {}) }),
}
