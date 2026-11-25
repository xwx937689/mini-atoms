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

/**
 * Inject a memory-backed storage shim so generated apps that call localStorage
 * do not crash inside our sandboxed iframe (sandbox has no allow-same-origin).
 * The shim lives only in the preview document; it cannot touch the host app's data.
 */
export function preparePreview(html: string): string {
  const shim = '<scr' + 'ipt>(function(){' +
    'var m={};function ls(){return{' +
    'getItem:function(k){return Object.prototype.hasOwnProperty.call(m,k)?m[k]:null;},' +
    'setItem:function(k,v){m[k]=String(v);},' +
    'removeItem:function(k){delete m[k];},' +
    'clear:function(){m={};},' +
    'key:function(i){var ks=Object.keys(m);return i<ks.length?ks[i]:null;},' +
    'get length(){return Object.keys(m).length;}' +
    '};}' +
    'try{if(!window.localStorage)throw 0;}catch(e){' +
    'try{Object.defineProperty(window,"localStorage",{value:ls(),configurable:true});}' +
    'catch(e2){window.localStorage=ls();}}' +
    'try{if(!window.sessionStorage)throw 0;}catch(e){' +
    'try{Object.defineProperty(window,"sessionStorage",{value:ls(),configurable:true});}' +
    'catch(e2){window.sessionStorage=ls();}}' +
    '})();</scr' + 'ipt>';
  if (/<head[^>]*>/i.test(html)) {
    return html.replace(/(<head[^>]*>)/i, '$1' + shim);
  }
  return shim + html;
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
