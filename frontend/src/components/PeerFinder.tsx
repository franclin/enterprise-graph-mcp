import { useState } from 'react'
import { api } from '../api'
import type { PeerMatch } from '../types'

export function PeerFinder() {
  const [skill, setSkill] = useState('python')
  const [timezone, setTimezone] = useState('Europe/Dublin')
  const [results, setResults] = useState<PeerMatch[] | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState<string | null>(null)

  async function onSubmit(e: React.FormEvent) {
    e.preventDefault()
    setLoading(true)
    setError(null)
    try {
      setResults(await api.findPeers({ skill, timezone }))
    } catch (err) {
      setError((err as Error).message)
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="space-y-4">
      <form onSubmit={onSubmit} className="flex flex-wrap items-end gap-3">
        <div>
          <label className="block text-sm font-medium mb-1">Skill</label>
          <input
            value={skill}
            onChange={e => setSkill(e.target.value)}
            className="border rounded px-3 py-2 text-sm"
          />
        </div>
        <div>
          <label className="block text-sm font-medium mb-1">Timezone</label>
          <input
            value={timezone}
            onChange={e => setTimezone(e.target.value)}
            className="border rounded px-3 py-2 text-sm"
          />
        </div>
        <button
          type="submit"
          disabled={loading}
          className="rounded bg-slate-900 text-white px-4 py-2 text-sm disabled:opacity-50"
        >
          {loading ? 'Searching…' : 'Find peers'}
        </button>
      </form>

      {error && <div className="text-red-600 text-sm">{error}</div>}

      {results && (
        <div className="rounded border bg-white divide-y">
          {results.length === 0 && (
            <div className="p-4 text-sm text-slate-500">No matches.</div>
          )}
          {results.map(r => (
            <div key={r.dis_id} className="p-4 space-y-1">
              <div className="flex items-center gap-2">
                <span className="font-medium">{r.display_name}</span>
                <span className="text-xs text-slate-500">{r.team_id}</span>
                {r.cross_team && (
                  <span className="text-xs bg-indigo-100 text-indigo-800 px-2 py-0.5 rounded">
                    cross-team
                  </span>
                )}
              </div>
              <div className="text-sm text-slate-600">{r.reason}</div>
              <div className="text-xs text-slate-500">
                Local time: {new Date(r.local_time).toLocaleString()} · {r.timezone}
              </div>
            </div>
          ))}
        </div>
      )}
    </div>
  )
}