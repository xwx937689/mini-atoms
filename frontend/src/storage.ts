/** Browser-side persistence mirror. Survives backend restarts on free hosting. */
import type { Message, Project } from './api'

const KEY = 'miniatoms:workspace:v1'

export interface LocalSnapshot {
  projects: Project[]
  details: Record<string, {
    messages: Message[]
    versions: { id: number; version_no: number; label: string; created_at: string; html_len: number }[]
    html: string
    name: string
    original_prompt: string
  }>
}

const EMPTY: LocalSnapshot = { projects: [], details: {} }

export function loadSnapshot(): LocalSnapshot {
  try {
    const raw = localStorage.getItem(KEY)
    if (!raw) return EMPTY
    const d = JSON.parse(raw)
    return { projects: d.projects || [], details: d.details || {} }
  } catch {
    return EMPTY
  }
}

export function saveSnapshot(snap: LocalSnapshot) {
  try {
    localStorage.setItem(KEY, JSON.stringify(snap))
  } catch (e) {
    // Quota exceeded - drop oldest detail HTML entries and retry once.
    const entries = Object.entries(snap.details)
    const trimmed = { ...snap, details: Object.fromEntries(entries.slice(-6)) }
    try { localStorage.setItem(KEY, JSON.stringify(trimmed)) } catch { /* give up quietly */ }
  }
}

export function upsertProject(project: Project) {
  const snap = loadSnapshot()
  const idx = snap.projects.findIndex(p => p.id === project.id)
  if (idx >= 0) snap.projects[idx] = { ...snap.projects[idx], ...project }
  else snap.projects.unshift(project)
  saveSnapshot(snap)
}

export function saveDetail(projectId: string, data: LocalSnapshot['details'][string]) {
  const snap = loadSnapshot()
  snap.details[projectId] = data
  if (!snap.projects.find(p => p.id === projectId)) {
    snap.projects.unshift({
      id: projectId,
      name: data.name,
      original_prompt: data.original_prompt,
      current_html: data.html,
      created_at: new Date().toISOString(),
      updated_at: new Date().toISOString(),
    })
  }
  saveSnapshot(snap)
}

export function getDetail(projectId: string) {
  return loadSnapshot().details[projectId]
}
