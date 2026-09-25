import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Employee, Presence } from '../types'

const statusColor: Record<Presence['status'], string> = {
  active: 'bg-green-100 text-green-800',
  away: 'bg-yellow-100 text-yellow-800',
  dnd: 'bg-red-100 text-red-800',
  offline: 'bg-slate-100 text-slate-600',
}

export function PresenceCard() {
  const [employees, setEmployees] = useState<Employee[]>([])
  const [selected, setSelected] = useState('')
  const [presence, setPresence] = useState<Presence | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.listEmployees().then(setEmployees).catch(console.error)
  }, [])

  useEffect(() => {
    if (!selected) return
    setError(null)
    api.getPresence(selected).then(setPresence).catch(e => setError(e.message))
  }, [selected])

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium mb-1">Employee</label>
        <select
          value={selected}
          onChange={e => setSelected(e.target.value)}
          className="border rounded px-3 py-2 text-sm min-w-[240px]"
        >
          <option value="">Select an employee…</option>
          {employees.map(e => (
            <option key={e.dis_id} value={e.dis_id}>
              {e.display_name} — {e.team_id}
            </option>
          ))}
        </select>
      </div>

      {error && <div className="text-red-600 text-sm">{error}</div>}

      {presence && (
        <div className="rounded border bg-white p-4 space-y-2">
          <div className="flex items-center gap-3">
            <span className="font-medium">{presence.display_name}</span>
            <span
              className={
                'text-xs px-2 py-0.5 rounded ' + statusColor[presence.status]
              }
            >
              {presence.status}
            </span>
          </div>
          <div className="text-sm text-slate-600">
            Local time: {new Date(presence.local_time).toLocaleString()}
          </div>
          <div className="text-sm text-slate-600">
            Timezone: {presence.timezone} · Hours {presence.working_hours}
          </div>
        </div>
      )}
    </div>
  )
}