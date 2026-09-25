import { useEffect, useState } from 'react'
import { api } from '../api'
import type { Team, TeamTopology as Topology } from '../types'

export function TeamTopology({ teams }: { teams: Team[] }) {
  const [selected, setSelected] = useState<string>('')
  const [topology, setTopology] = useState<Topology | null>(null)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    if (!selected) return
    setError(null)
    api.getTopology(selected).then(setTopology).catch(e => setError(e.message))
  }, [selected])

  return (
    <div className="space-y-4">
      <div>
        <label className="block text-sm font-medium mb-1">Team</label>
        <select
          value={selected}
          onChange={e => setSelected(e.target.value)}
          className="border rounded px-3 py-2 text-sm min-w-[200px]"
        >
          <option value="">Select a team…</option>
          {teams.map(t => (
            <option key={t.team_id} value={t.team_id}>
              {t.team_id}
            </option>
          ))}
        </select>
      </div>

      {error && <div className="text-red-600 text-sm">{error}</div>}

      {topology && (
        <div className="rounded border bg-white">
          <div className="border-b px-4 py-2 font-medium">
            {topology.team_name}
          </div>
          <ul className="divide-y">
            {topology.members.map((m, i) => (
              <li key={i} className="px-4 py-2 text-sm flex justify-between">
                <span>
                  <span className="font-medium">{String(m.display_name)}</span>{' '}
                  <span className="text-slate-500">({String(m.dis_id)})</span>
                </span>
                <span className="text-slate-500">{String(m.timezone)}</span>
              </li>
            ))}
          </ul>
        </div>
      )}
    </div>
  )
}