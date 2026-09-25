export interface Team { team_id: string }

export interface Employee {
  dis_id: string
  display_name: string
  team_id: string
  timezone: string
}

export interface Presence {
  dis_id: string
  display_name: string
  status: 'active' | 'away' | 'dnd' | 'offline'
  local_time: string
  timezone: string
  working_hours: string
  next_meeting: string | null
}

export interface PeerMatch {
  dis_id: string
  display_name: string
  team_id: string
  timezone: string
  local_time: string
  skill_level: number
  timezone_overlap_hours: number
  cross_team: boolean
  reason: string
}

export interface BuddyPairing {
  new_hire_id: string
  new_hire_name: string
  buddy_id: string
  buddy_name: string
  buddy_team_id: string
  match_score: number
  shared_skills: string[]
  seat_proximity: string | null
  reason: string
}

export interface TeamTopology {
  team_id: string
  team_name: string
  members: Array<Record<string, unknown>>
  cross_team_edges: Array<Record<string, unknown>>
}