import type {
  Team, Employee, Presence, PeerMatch, BuddyPairing, TeamTopology,
} from './types'

const base = ''

async function json<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(base + path, {
    headers: { 'Content-Type': 'application/json' },
    ...init,
  })
  if (!res.ok) {
    const body = await res.text()
    throw new Error(`${res.status} ${res.statusText}: ${body}`)
  }
  return res.json() as Promise<T>
}

export const api = {
  listTeams: () => json<Team[]>('/api/teams'),
  listEmployees: (teamId?: string) =>
    json<Employee[]>(`/api/employees${teamId ? `?team_id=${teamId}` : ''}`),
  getTopology: (teamId: string) =>
    json<TeamTopology>(`/api/teams/${teamId}/topology`),
  getPresence: (disId: string) =>
    json<Presence>(`/api/employees/${disId}/presence`),
  findPeers: (body: {
    skill: string; timezone: string; exclude_team_id?: string; max_results?: number
  }) => json<PeerMatch[]>('/api/tools/find-cross-functional-peers', {
    method: 'POST',
    body: JSON.stringify(body),
  }),
  findBuddy: (body: { new_hire_id: string; cohort_size?: number }) =>
    json<BuddyPairing[]>('/api/tools/onboarding-buddy-pairings', {
      method: 'POST',
      body: JSON.stringify(body),
    }),
}