import { useEffect, useState } from 'react'
import { api } from '../api'
import type { BuddyPairing as Pairing, Employee } from '../types'

export function BuddyPairing() {
  const [employees, setEmployees] = useState<Employee[]>([])
  const [newHireId, setNewHireId] = useState('')
  const [results, setResults] = useState<Pairing[] | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    api.listEmployees().then(setEmployees).catch(console.error)
  }, [])

  async function run() {
    if (!newHireId) return
    setError(null)
    try {
      setResults(await api.findBuddy({ new_hire_id: newHireId }))
    } catch (e) {
      setError((e as Error).message)
    }
  }

  return (
    <div className="space-y-4">
      <div className="flex items-end gap-3">
        <div>
          <label className="block text-sm font-medium mb-1">New hire</label>
          <select
            value={newHireId}
            onChange={e => setNewHireId(e.target.value)}
            className="border rounded px-3 py-2 text-sm min-w-[240px]"
          >
            <option value="">Select…</option>
            {employees.map(e => (
              <option key={e.dis_id} value={e.dis_id}>
                {e.display_name} — {e.team_id}
              </option>
            ))}
          </select>
        </div>
        <button
          onClick={run}
          disabled={!newHireId}
          className="rounded bg-slate-900 text-white px-4 py-2 text-sm disabled:opacity-50"
        >
          Find buddy
        </button>
      </div>

      {error && <div className="text-red-600 text-sm">{error}</div>}

      {results && (
        <div className="rounded border bg-white divide-y">
          {results.length === 0 && (
            <div className="p-4 text-sm text-slate-500">No matches.</div>
          )}
          {results.map(r => (
            <div key={r.buddy_id} className="p-4 space-y-1">
              <div className="font-medium">
                {r.new_hire_name} → {r.buddy_name}
              </div>
              <div className="text-sm text-slate-600">{r.reason}</div>
              <div className="text-xs text-slate-500">
                Match score: {(r.match_score * 100).toFixed(1)}%
                {r.seat_proximity ? ` · ${r.seat_proximity}` : ''}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}