export interface Project {
  id: string
  name: string
  original_prompt: string
  current_html: string
  created_at: string
  updated_at: string
  version_count?: number
  messages?: Message[]
  versions?: Version[]
}

export interface Activity {
  agent: string
  action: string
  tool: string
  detail: string
}

export interface Plan {
  app_name: string
  summary: string
  pages: string[]
  features: string[]
  layout: string
  components: string[]
  data: string[]
  style: string
  acceptance: string[]
}

export interface Message {
  id?: number
  role: 'user' | 'agent'
  agent: string
  kind: string
  content: string
  extra?: {
    plan?: Plan
    steps?: Activity[]
    version_no?: number
    slug?: string
  }
  created_at?: string
}

export interface Version {
  id: number
  version_no: number
  label: string
  created_at: string
  html_len: number
}

async function req<T>(path: string, options?: RequestInit): Promise<T> {
  const res = await fetch(path, {
    ...options,
    headers: { 'Content-Type': 'application/json', ...(options?.headers || {}) },
  })
  if (!res.ok) {
    let msg = `${res.status} ${res.statusText}`
    try { msg = (await res.json()).error || msg } catch { /* ignore */ }
    throw new Error(msg)
  }
  return res.json()
}

export const api = {
  config: () => req<{ llm_ready: boolean; model: string; mock_mode: boolean }>('/api/config'),
  listProjects: () => req<Project[]>('/api/projects'),
  createProject: (name: string, prompt = '') =>
    req<Project>('/api/projects', { method: 'POST', body: JSON.stringify({ name, prompt }) }),
  getProject: (id: string) => req<Project>(`/api/projects/${id}`),
  renameProject: (id: string, name: string) =>
    req<Project>(`/api/projects/${id}/rename`, { method: 'PATCH', body: JSON.stringify({ name }) }),
  deleteProject: (id: string) => req<{ ok: boolean }>(`/api/projects/${id}`, { method: 'DELETE' }),
  generate: (project_id: string, prompt: string) =>
    req<{ ok: boolean; version_no: number; plan: Plan; html: string; steps: Activity[]; mock_mode: boolean }>(
      '/api/generate', { method: 'POST', body: JSON.stringify({ project_id, prompt }) }
    ),
  edit: (project_id: string, instruction: string) =>
    req<{ ok: boolean; version_no: number; html: string; steps: Activity[] }>(
      '/api/edit', { method: 'POST', body: JSON.stringify({ project_id, instruction }) }
    ),
  versions: (project_id: string) => req<Version[]>(`/api/projects/${project_id}/versions`),
  rollback: (project_id: string, version_no: number) =>
    req<{ ok: boolean; html: string; version_no: number }>(
      '/api/rollback', { method: 'POST', body: JSON.stringify({ project_id, version_no }) }
    ),
  publish: (project_id: string, title?: string) =>
    req<{ ok: boolean; slug: string; url: string }>(
      '/api/publish', { method: 'POST', body: JSON.stringify({ project_id, title }) }
    ),
}
