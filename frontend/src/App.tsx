import { useEffect, useState } from 'react'
import { api } from './api'
import type { Team } from './types'
import { TeamTopology } from './components/TeamTopology'
import { PresenceCard } from './components/PresenceCard'
import { PeerFinder } from './components/PeerFinder'
import { BuddyPairing } from './components/BuddyPairing'

type Tab = 'topology' | 'presence' | 'peers' | 'buddy'

export default function App() {
  const [tab, setTab] = useState<Tab>('topology')
  const [teams, setTeams] = useState<Team[]>([])

  useEffect(() => {
    api.listTeams().then(setTeams).catch(console.error)
  }, [])

  return (
    <div className="min-h-screen bg-slate-50 text-slate-900">
      <header className="border-b bg-white">
        <div className="mx-auto max-w-5xl px-6 py-4">
          <h1 className="text-xl font-semibold">Enterprise Graph & Directory</h1>
          <p className="text-sm text-slate-500">
            Explore teams, presence, and cross-functional peers.
          </p>
        </div>
      </header>

      <nav className="border-b bg-white">
        <div className="mx-auto flex max-w-5xl gap-1 px-6">
          {(
            [
              ['topology', 'Team Topology'],
              ['presence', 'Presence'],
              ['peers', 'Find Peers'],
              ['buddy', 'Onboarding Buddy'],
            ] as const
          ).map(([key, label]) => (
            <button
              key={key}
              onClick={() => setTab(key)}
              className={
                'px-4 py-3 text-sm font-medium border-b-2 ' +
                (tab === key
                  ? 'border-slate-900 text-slate-900'
                  : 'border-transparent text-slate-500 hover:text-slate-800')
              }
            >
              {label}
            </button>
          ))}
        </div>
      </nav>

      <main className="mx-auto max-w-5xl px-6 py-8">
        {tab === 'topology' && <TeamTopology teams={teams} />}
        {tab === 'presence' && <PresenceCard />}
        {tab === 'peers' && <PeerFinder />}
        {tab === 'buddy' && <BuddyPairing />}
      </main>
    </div>
  )
}