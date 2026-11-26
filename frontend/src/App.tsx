import { useCallback, useEffect, useRef, useState } from 'react'
import {
  Atom, Plus, MessageSquare, PanelLeftClose, PanelLeftOpen, Send, Code2, Eye,
  ChevronDown, ChevronRight, Copy, Check, RotateCcw, ExternalLink, Trash2,
  Loader2, Sparkles, Layers, CheckCircle2, Circle, AlertCircle, X, Globe2,
  Bot, User, Zap, GitBranch, Clock, MoreHorizontal, Pencil, RefreshCw,
} from 'lucide-react'
import { api, preparePreview, type Project, type Message, type Plan, type Activity, type Version, type Quality } from './api'
import { loadSnapshot, saveDetail, upsertProject, saveSnapshot } from './storage'

const AGENT_META: Record<string, { color: string; bg: string; short: string }> = {
  'Mike': { color: '#7c3aed', bg: '#f3f0ff', short: 'M' },
  'Team Leader': { color: '#7c3aed', bg: '#f3f0ff', short: 'M' },
  'Alex': { color: '#2563eb', bg: '#eff6ff', short: 'A' },
  'Engineer': { color: '#2563eb', bg: '#eff6ff', short: 'A' },
  'Reviewer': { color: '#059669', bg: '#ecfdf5', short: 'R' },
  'Architect': { color: '#d97706', bg: '#fffbeb', short: 'A' },
  'System': { color: '#6b7280', bg: '#f3f4f6', short: 'S' },
}

const AGENT_NAMES = ['Mike · Team Leader', 'Alex · Engineer', 'Reviewer', 'System']

function agentMeta(agent: string) {
  if (agent.includes('Mike')) return AGENT_META['Mike']
  if (agent.includes('Alex')) return AGENT_META['Alex']
  if (agent.includes('Reviewer')) return AGENT_META['Reviewer']
  if (agent.includes('Architect')) return AGENT_META['Architect']
  return AGENT_META['System']
}

function timeAgo(iso: string) {
  if (!iso) return ''
  const diff = Date.now() - new Date(iso).getTime()
  const m = Math.floor(diff / 60000)
  if (m < 1) return '刚刚'
  if (m < 60) return `${m} 分钟前`
  const h = Math.floor(m / 60)
  if (h < 24) return `${h} 小时前`
  return `${Math.floor(h / 24)} 天前`
}

const EXAMPLES = [
  {
    title: '接口自动化测试质量看板',
    desc: '指标、趋势、失败明细，主打演示',
    prompt: '帮我生成一个接口自动化测试质量看板，展示接口总数、测试通过率、失败数量、近7天执行趋势、失败用例分类。支持按服务筛选，点击失败用例查看详情。整体采用现代化后台管理界面。',
  },
  {
    title: 'SaaS 项目管理后台',
    desc: '任务、看板、成员协作',
    prompt: '生成一个 SaaS 项目管理后台，包含项目概览、任务看板（待办/进行中/已完成）、成员列表、进度统计。支持任务拖拽切换状态、按成员筛选、新增任务弹窗。界面现代简洁。',
  },
  {
    title: '店铺经营数据仪表盘',
    desc: '销售、转化、排行分析',
    prompt: '创建一个电商经营数据仪表盘，展示今日销售额、订单数、转化率、客单价，近30天销售趋势图、品类占比饼图、热销商品排行榜。支持时间范围切换。',
  },
]

export default function App() {
  const [projects, setProjects] = useState<Project[]>([])
  const [current, setCurrent] = useState<Project | null>(null)
  const [messages, setMessages] = useState<Message[]>([])
  const [versions, setVersions] = useState<Version[]>([])
  const [quality, setQuality] = useState<Quality | null>(null)
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [status, setStatus] = useState('')
  const [previewHtml, setPreviewHtml] = useState('')
  const [view, setView] = useState<'preview' | 'code'>('preview')
  const [sidebarOpen, setSidebarOpen] = useState(true)
  const [showVersions, setShowVersions] = useState(false)
  const [copied, setCopied] = useState(false)
  const [error, setError] = useState('')
  const [mockMode, setMockMode] = useState(false)
  const [publishUrl, setPublishUrl] = useState('')
  const [firstLoad, setFirstLoad] = useState(true)
  const chatRef = useRef<HTMLDivElement>(null)
  const inputRef = useRef<HTMLTextAreaElement>(null)

  const refreshProjects = useCallback(async () => {
    const list = await api.listProjects()
    setProjects(list)
    return list
  }, [])

  useEffect(() => {
    ;(async () => {
      try {
        const cfg = await api.config()
        setMockMode(cfg.mock_mode)
      } catch { /* ignore */ }
      try {
        const list = await refreshProjects()
        // 后端可达：以服务端项目列表为准，删除本地已不存在的幽灵项目
        const snap = loadSnapshot()
        const serverIds = new Set(list.map(p => p.id))
        const validProjects = list.length > 0
          ? list
          : snap.projects.filter(p => !serverIds.has(p.id))
        if (list.length > 0) {
          const validDetails = Object.fromEntries(
            Object.entries(snap.details).filter(([id]) => serverIds.has(id))
          )
          saveSnapshot({ projects: validProjects, details: validDetails })
        }
        if (list.length > 0) {
          await openProject(list[0].id)
        } else {
          restoreFromLocal()
        }
      } catch {
        restoreFromLocal()
      }
      setFirstLoad(false)
    })()
  }, [])

  function restoreFromLocal() {
    const snap = loadSnapshot()
    if (snap.projects.length > 0) {
      setProjects(snap.projects)
      const first = snap.projects[0]
      const d = snap.details[first.id]
      setCurrent(first)
      setMessages(d?.messages || [])
      setVersions(d?.versions || [])
      setPreviewHtml(d?.html || '')
    }
  }

  function mirrorDetail(p: Project, msgs: Message[], vers: Version[], html: string) {
    saveDetail(p.id, {
      messages: msgs, versions: vers, html,
      name: p.name, original_prompt: p.original_prompt,
    })
    upsertProject({ ...p, current_html: html })
  }

  useEffect(() => {
    chatRef.current?.scrollTo({ top: chatRef.current.scrollHeight, behavior: 'smooth' })
  }, [messages, status])

  async function openProject(id: string) {
    let p: Project
    try {
      p = await api.getProject(id)
    } catch {
      const local = loadSnapshot()
      const lp = local.projects.find(x => x.id === id)
      if (!lp) return
      p = lp
    }
    const localDetail = loadSnapshot().details[id]
    const msgs = (p.messages?.length ? p.messages : localDetail?.messages) || []
    const vers = (p.versions?.length ? p.versions : localDetail?.versions) || []
    const html = p.current_html || localDetail?.html || ''
    setCurrent(p)
    setMessages(msgs)
    setVersions(vers)
    setQuality(p.quality || null)
    setPreviewHtml(html)
    setView('preview')
    setError('')
    setPublishUrl('')
  }

  async function newProject() {
    const p = await api.createProject('未命名应用')
    await refreshProjects()
    await openProject(p.id)
    setInput(EXAMPLES[0].prompt)
    setTimeout(() => inputRef.current?.focus(), 100)
  }

  async function doGenerate(prompt: string) {
    if (!current || busy || !prompt.trim()) return
    setBusy(true)
    setError('')
    setStatus('正在分析需求...')
    const tempId = Date.now()
    setMessages(m => [...m, { id: tempId, role: 'user', agent: '', kind: 'generate', content: prompt }])

    try {
      setStatus('Agent 团队正在协作构建...')
      const res = await api.generate(current.id, prompt)
      setPreviewHtml(res.html)
      setView('preview')
      setStatus('')
      const detail: Message = {
        role: 'agent',
        agent: 'Alex · Engineer',
        kind: 'result',
        content: `已完成应用生成，共 ${res.plan.features?.length || 0} 项功能，版本 V${res.version_no}。`,
        extra: { plan: res.plan, steps: res.steps, version_no: res.version_no, quality: res.quality },
      }
      setMessages(m => [...m, detail])
      setMockMode(res.mock_mode)
      setQuality(res.quality || null)
      await Promise.all([refreshProjects(), api.getProject(current.id).then(p => {
        setVersions(p.versions || [])
        setCurrent(p)
        setMessages(p.messages || [])
        // Use server's authoritative messages - rebuilding from a stale closure
        // previously dropped the user's prompt message from the local mirror.
        mirrorDetail(p, p.messages || [], p.versions || [], res.html)
      })])
    } catch (e: any) {
      setError(e.message || '生成失败')
      setStatus('')
      setMessages(m => [...m, { role: 'agent', agent: 'System', kind: 'error', content: `生成失败：${e.message}` }])
    } finally {
      setBusy(false)
    }
  }

  async function doEdit(instruction: string) {
    if (!current || busy || !instruction.trim()) return
    setBusy(true)
    setError('')
    setStatus('正在理解修改要求...')
    setMessages(m => [...m, { role: 'user', agent: '', kind: 'edit', content: instruction }])
    try {
      setStatus('Agent 正在应用修改...')
      const res = await api.edit(current.id, instruction, previewHtml || undefined)
      setPreviewHtml(res.html)
      setStatus('')
      const resultMsg: Message = {
        role: 'agent', agent: 'Alex · Engineer', kind: 'result',
        content: `修改已完成，已生成版本 V${res.version_no}。`,
        extra: { steps: res.steps, version_no: res.version_no, quality: res.quality },
      }
      setMessages(m => [...m, resultMsg])
      setQuality(res.quality || null)
      await Promise.all([refreshProjects(), api.getProject(current.id).then(p => {
        setVersions(p.versions || [])
        setCurrent(p)
        setMessages(p.messages || [])
        mirrorDetail(p, p.messages || [], p.versions || [], res.html)
      })])
    } catch (e: any) {
      setError(e.message || '修改失败')
      setStatus('')
      setMessages(m => [...m, { role: 'agent', agent: 'System', kind: 'error', content: `修改失败（已保留上一版本）：${e.message}` }])
    } finally {
      setBusy(false)
    }
  }

  function submit() {
    if (!current) {
      setError('请先创建或选择一个项目')
      return
    }
    const text = input.trim()
    if (!text || busy) return
    setInput('')
    if (previewHtml) {
      doEdit(text)
    } else {
      doGenerate(text)
    }
  }

  async function doRollback(v: number) {
    if (!current) return
    setError('')
    try {
      const res = await api.rollback(current.id, v)
      setPreviewHtml(res.html)
      setShowVersions(false)
      await api.getProject(current.id).then(p => {
        setMessages(p.messages || [])
        setCurrent(p)
        mirrorDetail(p, p.messages || [], p.versions || [], res.html)
      })
    } catch (e: any) {
      setError(`回滚失败：${e.message || '未知错误'}`)
    }
  }

  async function doPublish() {
    if (!current) return
    setError('')
    setStatus('正在发布...')
    try {
      const res = await api.publish(current.id, current.name, previewHtml || undefined)
      setPublishUrl(`${location.origin}${res.url}`)
      await api.getProject(current.id).then(p => { setMessages(p.messages || []); setCurrent(p) })
    } catch (e: any) {
      setError(`发布失败：${e.message || '未知错误'}`)
    } finally {
      setStatus('')
    }
  }

  async function copyCode() {
    try {
      await navigator.clipboard.writeText(previewHtml)
      setCopied(true)
      setTimeout(() => setCopied(false), 1800)
    } catch { /* ignore */ }
  }

  function loadExample(idx: number) {
    setInput(EXAMPLES[idx].prompt)
    inputRef.current?.focus()
  }

  return (
    <div className="h-screen flex flex-col bg-white text-gray-900 overflow-hidden">
      {/* Top bar */}
      <header className="h-14 flex items-center gap-3 px-4 border-b border-gray-200 bg-white shrink-0 z-20">
        <div className="flex items-center gap-2.5 w-[236px] shrink-0">
          <div className="w-8 h-8 rounded-lg bg-gradient-to-br from-indigo-600 to-violet-600 flex items-center justify-center">
            <Atom size={19} className="text-white" />
          </div>
          <div>
            <div className="text-[15px] font-semibold tracking-tight leading-none">Mini Atoms</div>
            <div className="text-[10px] text-gray-400 mt-1">AI 应用生成工作台</div>
          </div>
        </div>
        <div className="flex-1 flex items-center gap-2 min-w-0">
          <button onClick={() => setSidebarOpen(s => !s)} className="p-1.5 rounded-md hover:bg-gray-100 text-gray-500" title="切换侧栏">
            {sidebarOpen ? <PanelLeftClose size={17} /> : <PanelLeftOpen size={17} />}
          </button>
          <div className="h-4 w-px bg-gray-200" />
          {current ? (
            <>
              <span className="font-medium text-sm truncate">{current.name}</span>
              <span className="text-[11px] text-gray-400 shrink-0">· 版本 {versions.length || (current.current_html ? 1 : 0)}</span>
              {current.current_html && (
                <span className="flex items-center gap-1 text-[11px] text-emerald-600 bg-emerald-50 px-2 py-0.5 rounded-full ml-1">
                  <span className="w-1.5 h-1.5 rounded-full bg-emerald-500" />已生成
                </span>
              )}
              {quality && (
                <span className="flex items-center gap-1 text-[11px] bg-indigo-50 text-indigo-700 px-2 py-0.5 rounded-full ml-1" title="生成应用质量评分">
                  质量 {quality.total} · {quality.grade}
                </span>
              )}
            </>
          ) : (
            <span className="text-sm text-gray-400">未选择项目</span>
          )}
        </div>
        <div className="flex items-center gap-2 shrink-0">
          {mockMode && (
            <span className="text-[11px] text-amber-600 bg-amber-50 px-2 py-1 rounded-md flex items-center gap-1">
              <Zap size={11} /> 演示模式
            </span>
          )}
          {current?.current_html && (
            <>
              <button onClick={() => setShowVersions(s => !s)} className="flex items-center gap-1.5 px-2.5 py-1.5 text-[13px] text-gray-600 hover:bg-gray-100 rounded-lg border border-gray-200">
                <GitBranch size={14} /> 版本
              </button>
              <button onClick={doPublish} disabled={busy} className="flex items-center gap-1.5 px-2.5 py-1.5 text-[13px] text-white bg-indigo-600 hover:bg-indigo-700 rounded-lg disabled:opacity-50">
                <Globe2 size={14} /> 发布
              </button>
            </>
          )}
        </div>
      </header>

      <div className="flex flex-1 min-h-0">
        {/* Sidebar */}
        {sidebarOpen && (
          <aside className="w-[236px] shrink-0 border-r border-gray-200 bg-[#fafafa] flex flex-col">
            <div className="p-3">
              <button onClick={newProject} className="w-full flex items-center justify-center gap-2 px-3 py-2.5 bg-gray-900 hover:bg-gray-800 text-white text-[13px] font-medium rounded-lg transition-colors">
                <Plus size={16} /> 新建应用
              </button>
            </div>
            <div className="px-4 pb-2 text-[11px] font-medium text-gray-400 uppercase tracking-wider">项目列表</div>
            <div className="flex-1 overflow-y-auto px-2 pb-3">
              {projects.length === 0 && (
                <div className="px-3 py-8 text-center text-[12px] text-gray-400 leading-relaxed">
                  暂无项目<br />点击上方新建应用开始
                </div>
              )}
              {projects.map(p => (
                <button
                  key={p.id}
                  onClick={() => openProject(p.id)}
                  className={`w-full text-left px-3 py-2.5 rounded-lg mb-1 group transition-colors ${
                    current?.id === p.id ? 'bg-white border border-gray-200 shadow-sm' : 'hover:bg-gray-100 border border-transparent'
                  }`}
                >
                  <div className="flex items-center gap-2">
                    <Layers size={14} className={current?.id === p.id ? 'text-indigo-600' : 'text-gray-400'} />
                    <span className="text-[13px] font-medium truncate flex-1">{p.name}</span>
                  </div>
                  <div className="flex items-center gap-1.5 mt-1.5 text-[11px] text-gray-400 pl-6">
                    <Clock size={10} /> {timeAgo(p.updated_at)}
                    {!!p.version_count && <span>· {p.version_count} 版本</span>}
                  </div>
                </button>
              ))}
            </div>
            <div className="border-t border-gray-200 px-4 py-3">
              <div className="flex items-center gap-2 text-[11px] text-gray-400">
                <Sparkles size={12} className="text-indigo-500" />
                Agent 团队 · {mockMode ? '演示模式' : 'DeepSeek 驱动'}
              </div>
            </div>
          </aside>
        )}

        {/* Chat */}
        <section className="w-[430px] shrink-0 flex flex-col border-r border-gray-200 bg-white min-w-0">
          <div className="h-11 flex items-center gap-2 px-4 border-b border-gray-100 shrink-0">
            <MessageSquare size={15} className="text-gray-500" />
            <span className="text-[13px] font-medium">对话构建</span>
            <span className="text-[11px] text-gray-400 ml-auto">描述需求 · 持续迭代</span>
          </div>

          <div ref={chatRef} className="flex-1 overflow-y-auto px-4 py-4 space-y-4">
            {!current && (
              <div className="h-full flex flex-col items-center justify-center text-center px-4">
                <div className="w-12 h-12 rounded-xl bg-indigo-50 flex items-center justify-center mb-3">
                  <Sparkles size={24} className="text-indigo-600" />
                </div>
                <h3 className="font-medium text-[15px] mb-1">从一个想法开始</h3>
                <p className="text-[12px] text-gray-400 leading-relaxed">新建应用后用自然语言描述你想要的网页，Agent 团队会帮你生成。</p>
              </div>
            )}

            {current && messages.length === 0 && (
              <div className="fade-up">
                <div className="flex items-start gap-2.5 mb-4">
                  <div className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0" style={{ background: AGENT_META['Mike'].bg }}>
                    <Bot size={15} style={{ color: AGENT_META['Mike'].color }} />
                  </div>
                  <div>
                    <div className="text-[12px] font-medium mb-1">Mike <span className="text-gray-400 font-normal">· Team Leader</span></div>
                    <div className="text-[13px] text-gray-600 leading-relaxed">
                      你好！我是团队负责人。告诉我你想构建什么应用，我会拆解需求并交给工程师实现。
                    </div>
                  </div>
                </div>
                <div className="text-[11px] text-gray-400 mb-2 px-1">试试这些示例：</div>
                <div className="space-y-2">
                  {EXAMPLES.map((ex, i) => (
                    <button key={i} onClick={() => loadExample(i)} className="w-full text-left px-3 py-2.5 rounded-lg border border-gray-200 hover:border-indigo-300 hover:bg-indigo-50/40 transition-colors group">
                      <div className="text-[13px] font-medium text-gray-800 group-hover:text-indigo-700">{ex.title}</div>
                      <div className="text-[11px] text-gray-400 mt-0.5">{ex.desc}</div>
                    </button>
                  ))}
                </div>
              </div>
            )}

            {messages.map((m, idx) => (
              <MessageItem key={m.id ?? `m-${idx}`} msg={m} />
            ))}

            {status && (
              <div className="flex items-start gap-2.5 fade-up">
                <div className="w-7 h-7 rounded-lg bg-blue-50 flex items-center justify-center shrink-0">
                  <Loader2 size={15} className="text-blue-600 animate-spin" />
                </div>
                <div className="flex-1">
                  <div className="text-[12px] font-medium mb-1.5">Agent 团队工作中</div>
                  <div className="text-[12px] text-gray-600">{status}</div>
                  <div className="flex items-center gap-1 mt-2.5">
                    {['Mike', 'Alex', 'Reviewer'].map((a, i) => (
                      <span key={a} className="w-1.5 h-1.5 rounded-full dot-loading" style={{ background: agentMeta(a).color, animationDelay: `${i * 0.2}s` }} />
                    ))}
                    <span className="text-[11px] text-gray-400 ml-2">正在协作</span>
                  </div>
                </div>
              </div>
            )}

            {error && (
              <div className="flex items-start gap-2 px-3 py-2.5 bg-red-50 border border-red-200 rounded-lg text-[12px] text-red-700">
                <AlertCircle size={15} className="shrink-0 mt-0.5" />
                <span className="flex-1">{error}</span>
                <button onClick={() => setError('')}><X size={14} /></button>
              </div>
            )}
          </div>

          <div className="p-3 border-t border-gray-200 shrink-0">
            <div className="border border-gray-200 rounded-xl focus-within:border-indigo-400 focus-within:ring-2 focus-within:ring-indigo-50 transition-all bg-white">
              <textarea
                ref={inputRef}
                value={input}
                onChange={e => setInput(e.target.value)}
                onKeyDown={e => {
                  if (e.key === 'Enter' && !e.shiftKey) {
                    e.preventDefault()
                    submit()
                  }
                }}
                placeholder={current ? (previewHtml ? '描述你想修改的地方...' : '描述你想生成的应用...') : '请先新建应用'}
                disabled={!current || busy}
                rows={3}
                className="w-full resize-none px-3.5 pt-3 pb-1 text-[13px] outline-none placeholder:text-gray-400 disabled:bg-gray-50"
              />
              <div className="flex items-center justify-between px-3 pb-2.5 pt-1">
                <span className="text-[11px] text-gray-400">{previewHtml ? 'Enter 发送修改指令' : 'Enter 发送 · Shift+Enter 换行'}</span>
                <button onClick={submit} disabled={!current || busy || !input.trim()} className="w-8 h-8 rounded-lg bg-indigo-600 hover:bg-indigo-700 disabled:bg-gray-200 disabled:text-gray-400 text-white flex items-center justify-center transition-colors">
                  {busy ? <Loader2 size={15} className="animate-spin" /> : <Send size={14} />}
                </button>
              </div>
            </div>
          </div>
        </section>

        {/* Preview */}
        <section className="flex-1 flex flex-col bg-[#f5f6f8] min-w-0">
          <div className="h-11 flex items-center gap-1 px-3 border-b border-gray-200 bg-white shrink-0">
            <div className="flex items-center gap-0.5 p-0.5 bg-gray-100 rounded-lg">
              <button onClick={() => setView('preview')} className={`flex items-center gap-1.5 px-3 py-1.5 text-[12px] rounded-md transition-colors ${view === 'preview' ? 'bg-white shadow-sm text-gray-900 font-medium' : 'text-gray-500 hover:text-gray-700'}`}>
                <Eye size={13} /> 预览
              </button>
              <button onClick={() => setView('code')} className={`flex items-center gap-1.5 px-3 py-1.5 text-[12px] rounded-md transition-colors ${view === 'code' ? 'bg-white shadow-sm text-gray-900 font-medium' : 'text-gray-500 hover:text-gray-700'}`}>
                <Code2 size={13} /> 代码
              </button>
            </div>
            <div className="ml-auto flex items-center gap-1.5">
              {view === 'code' && previewHtml && (
                <button onClick={copyCode} className="flex items-center gap-1.5 px-2.5 py-1.5 text-[12px] text-gray-600 hover:bg-gray-100 rounded-md">
                  {copied ? <Check size={13} className="text-emerald-600" /> : <Copy size={13} />} {copied ? '已复制' : '复制'}
                </button>
              )}
              {previewHtml && (
                <button onClick={() => setPreviewHtml(h => h + ' ')} className="flex items-center gap-1.5 px-2.5 py-1.5 text-[12px] text-gray-600 hover:bg-gray-100 rounded-md" title="刷新预览">
                  <RefreshCw size={13} /> 刷新
                </button>
              )}
              {publishUrl && (
                <a href={publishUrl} target="_blank" rel="noreferrer" className="flex items-center gap-1.5 px-2.5 py-1.5 text-[12px] text-indigo-600 hover:bg-indigo-50 rounded-md">
                  <ExternalLink size={13} /> 打开链接
                </a>
              )}
            </div>
          </div>

          {showVersions && current && (
            <div className="bg-white border-b border-gray-200 px-4 py-3 shrink-0">
              <div className="flex items-center justify-between mb-2">
                <div className="flex items-center gap-1.5 text-[12px] font-medium"><GitBranch size={13} /> 版本历史</div>
                <button onClick={() => setShowVersions(false)} className="text-gray-400 hover:text-gray-600"><X size={14} /></button>
              </div>
              <div className="flex flex-wrap gap-2 max-h-24 overflow-y-auto">
                {versions.length === 0 && <span className="text-[12px] text-gray-400">暂无版本</span>}
                {versions.map(v => (
                  <button key={v.id} onClick={() => doRollback(v.version_no)} className="flex items-center gap-1.5 px-2.5 py-1.5 text-[11px] border border-gray-200 rounded-lg hover:border-indigo-300 hover:bg-indigo-50/50 group">
                    <span className="font-medium text-gray-700">V{v.version_no}</span>
                    <span className="text-gray-400 max-w-[160px] truncate">{v.label.replace(/^V\d+: /, '')}</span>
                    <RotateCcw size={11} className="text-gray-300 group-hover:text-indigo-500" />
                  </button>
                ))}
              </div>
            </div>
          )}

          <div className="flex-1 min-h-0 p-4">
            {!previewHtml ? (
              <div className="h-full rounded-xl border-2 border-dashed border-gray-200 bg-white/60 flex flex-col items-center justify-center">
                <div className="w-14 h-14 rounded-2xl bg-gray-100 flex items-center justify-center mb-4">
                  <Eye size={26} className="text-gray-400" />
                </div>
                <div className="text-[14px] font-medium text-gray-600 mb-1.5">应用预览将在这里显示</div>
                <div className="text-[12px] text-gray-400 text-center max-w-[280px] leading-relaxed">
                  在左侧描述你的需求，Agent 生成网页后会实时渲染在这里
                </div>
              </div>
            ) : view === 'preview' ? (
              <div className="h-full rounded-xl border border-gray-200 bg-white overflow-hidden shadow-sm flex flex-col">
                <div className="h-9 flex items-center gap-2 px-3 border-b border-gray-100 bg-gray-50/80 shrink-0">
                  <div className="flex gap-1.5">
                    <span className="w-2.5 h-2.5 rounded-full bg-[#ff5f57]" />
                    <span className="w-2.5 h-2.5 rounded-full bg-[#febc2e]" />
                    <span className="w-2.5 h-2.5 rounded-full bg-[#28c840]" />
                  </div>
                  <div className="flex-1 mx-3 h-6 rounded-md bg-white border border-gray-200 flex items-center px-2.5 text-[10px] text-gray-400 truncate">
                    <Globe2 size={10} className="mr-1.5 shrink-0" /> preview://{current?.id || 'app'}.mini-atoms.local
                  </div>
                </div>
                <iframe
                  key={previewHtml.length}
                  srcDoc={preparePreview(previewHtml)}
                  sandbox="allow-scripts allow-forms allow-modals"
                  title="Generated app preview"
                  className="flex-1 w-full border-0"
                />
              </div>
            ) : (
              <div className="h-full rounded-xl border border-gray-200 bg-[#1e1e2e] overflow-hidden shadow-sm flex flex-col">
                <div className="h-9 flex items-center px-4 border-b border-white/10 shrink-0">
                  <span className="text-[11px] text-gray-400 font-mono">index.html · {previewHtml.split('\n').length} 行</span>
                </div>
                <pre className="flex-1 overflow-auto p-4 m-0 text-[11.5px] leading-relaxed text-[#cdd6f4] font-mono whitespace-pre-wrap break-all">
                  {previewHtml}
                </pre>
              </div>
            )}
          </div>
        </section>
      </div>
    </div>
  )
}

/* ---------- Message components ---------- */

function MessageItem({ msg }: { msg: Message }) {
  if (msg.role === 'user') {
    return (
      <div className="flex justify-end fade-up">
        <div className="max-w-[88%]">
          <div className="flex items-center justify-end gap-1.5 mb-1">
            <span className="text-[11px] text-gray-400">{msg.kind === 'edit' ? '修改指令' : '需求'}</span>
            <div className="w-6 h-6 rounded-md bg-gray-800 flex items-center justify-center">
              <User size={13} className="text-white" />
            </div>
          </div>
          <div className="px-3.5 py-2.5 bg-indigo-600 text-white text-[13px] leading-relaxed rounded-xl rounded-tr-sm">
            {msg.content}
          </div>
        </div>
      </div>
    )
  }

  const meta = agentMeta(msg.agent)
  return (
    <div className="fade-up">
      {msg.kind === 'plan' ? (
        <PlanCard msg={msg} meta={meta} />
      ) : msg.kind === 'result' ? (
        <ResultCard msg={msg} meta={meta} />
      ) : msg.kind === 'error' ? (
        <div className="flex items-start gap-2.5">
          <div className="w-7 h-7 rounded-lg bg-red-50 flex items-center justify-center shrink-0"><AlertCircle size={15} className="text-red-600" /></div>
          <div className="flex-1 text-[12px] text-red-700 bg-red-50 rounded-lg px-3 py-2">{msg.content}</div>
        </div>
      ) : (
        <div className="flex items-start gap-2.5">
          <div className="w-7 h-7 rounded-lg flex items-center justify-center shrink-0" style={{ background: meta.bg }}>
            <Bot size={15} style={{ color: meta.color }} />
          </div>
          <div className="text-[13px] text-gray-700 leading-relaxed pt-1">{msg.content}</div>
        </div>
      )}
    </div>
  )
}

function AgentHeader({ name, meta, label }: { name: string; meta: { color: string; bg: string; short: string }; label: string }) {
  return (
    <div className="flex items-center gap-2 mb-2">
      <div className="w-7 h-7 rounded-lg flex items-center justify-center text-[12px] font-semibold shrink-0" style={{ background: meta.bg, color: meta.color }}>
        {meta.short}
      </div>
      <div className="flex-1 min-w-0">
        <div className="text-[12px] font-medium truncate" style={{ color: meta.color }}>{name}</div>
        <div className="text-[10px] text-gray-400">{label}</div>
      </div>
    </div>
  )
}

function PlanCard({ msg, meta }: { msg: Message; meta: { color: string; bg: string; short: string } }) {
  const [open, setOpen] = useState(false)
  const plan = msg.extra?.plan
  const steps = msg.extra?.steps || []
  if (!plan) return null
  return (
    <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
      <div className="px-3 py-2.5">
        <AgentHeader name="Mike · Team Leader" meta={meta} label="需求拆解 · 应用规划" />
        <div className="text-[12px] text-gray-600 leading-relaxed mb-2">{plan.summary}</div>
        <button onClick={() => setOpen(o => !o)} className="flex items-center gap-1 text-[11px] text-indigo-600 hover:text-indigo-700">
          {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
          {open ? '收起规划详情' : `查看规划详情 · ${plan.features?.length || 0} 项功能`}
        </button>
        {open && (
          <div className="mt-2.5 space-y-2.5 fade-up">
            {plan.features?.length > 0 && (
              <div>
                <div className="text-[11px] font-medium text-gray-500 mb-1">功能清单</div>
                <div className="space-y-1">
                  {plan.features.map((f, i) => (
                    <div key={i} className="flex items-start gap-1.5 text-[11px] text-gray-600">
                      <CheckCircle2 size={12} className="text-emerald-500 shrink-0 mt-0.5" /> {f}
                    </div>
                  ))}
                </div>
              </div>
            )}
            {plan.acceptance?.length > 0 && (
              <div>
                <div className="text-[11px] font-medium text-gray-500 mb-1">验收标准</div>
                <div className="space-y-1">
                  {plan.acceptance.map((f, i) => (
                    <div key={i} className="flex items-start gap-1.5 text-[11px] text-gray-600">
                      <Circle size={11} className="text-gray-400 shrink-0 mt-0.5" /> {f}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        )}
      </div>
      {steps.length > 0 && (
        <div className="border-t border-gray-100 bg-gray-50/60 px-3 py-2">
          <div className="flex items-center gap-1.5 text-[10px] text-gray-400 mb-1.5">
            <Zap size={11} /> 已处理 {steps.length} 步
          </div>
          <div className="space-y-1.5">
            {steps.map((s, i) => <ActivityRow key={i} step={s} />)}
          </div>
        </div>
      )}
    </div>
  )
}

function ActivityRow({ step }: { step: Activity }) {
  const meta = agentMeta(step.agent)
  return (
    <div className="flex items-start gap-2 text-[11px]">
      <span className="w-1.5 h-1.5 rounded-full mt-1.5 shrink-0" style={{ background: meta.color }} />
      <div className="flex-1 min-w-0">
        <span className="text-gray-700">{step.action}</span>
        {step.tool && (
          <span className="ml-1.5 px-1.5 py-0.5 rounded border border-gray-200 bg-white text-[10px] font-mono text-gray-500 whitespace-nowrap">
            {step.tool}
          </span>
        )}
      </div>
    </div>
  )
}

function ResultCard({ msg, meta }: { msg: Message; meta: { color: string; bg: string; short: string } }) {
  const [open, setOpen] = useState(false)
  const [qualityOpen, setQualityOpen] = useState(false)
  const steps = msg.extra?.steps || []
  const quality = msg.extra?.quality
  return (
    <div className="rounded-xl border border-gray-200 bg-white overflow-hidden">
      <div className="px-3 py-2.5">
        <AgentHeader name={msg.agent} meta={meta} label={msg.extra?.version_no ? `版本 V${msg.extra.version_no}` : '系统通知'} />
        <div className="text-[12px] text-gray-700 flex items-start gap-1.5">
          <CheckCircle2 size={13} className="text-emerald-500 shrink-0 mt-0.5" />
          {msg.content}
        </div>
        {quality && (
          <div className="mt-2 rounded-lg border border-indigo-100 bg-indigo-50/40 overflow-hidden">
            <button onClick={() => setQualityOpen(o => !o)} className="w-full flex items-center gap-2 px-2.5 py-2 text-left">
              <span className="text-[11px] font-medium text-gray-600">生成质量评分</span>
              <span className={`text-[13px] font-semibold ${quality.total >= 85 ? 'text-emerald-600' : quality.total >= 70 ? 'text-indigo-600' : 'text-amber-600'}`}>
                {quality.total}
              </span>
              <span className="text-[11px] text-gray-400">/ 100 · {quality.grade} 级</span>
              <span className="ml-auto text-indigo-500">{qualityOpen ? <ChevronDown size={13} /> : <ChevronRight size={13} />}</span>
            </button>
            {qualityOpen && (
              <div className="px-2.5 pb-2.5 space-y-1.5 fade-up">
                {quality.dimensions.map(d => (
                  <div key={d.key}>
                    <div className="flex items-center justify-between text-[10px] mb-0.5">
                      <span className="text-gray-600">{d.label} <span className="text-gray-400">· 权重 {d.weight}%</span></span>
                      <span className="font-medium text-gray-700">{d.score.toFixed(0)}</span>
                    </div>
                    <div className="h-1 rounded-full bg-gray-200 overflow-hidden">
                      <div className={`h-full rounded-full ${d.score >= 80 ? 'bg-emerald-500' : d.score >= 60 ? 'bg-indigo-500' : 'bg-amber-500'}`} style={{ width: `${d.score}%` }} />
                    </div>
                    <div className="text-[10px] text-gray-400 mt-0.5">{d.note}</div>
                  </div>
                ))}
                <div className="text-[10px] text-gray-400 pt-1 border-t border-indigo-100">启发式静态评估 · 供参考，非模型自评</div>
              </div>
            )}
          </div>
        )}
        {steps.length > 0 && (
          <button onClick={() => setOpen(o => !o)} className="flex items-center gap-1 text-[11px] text-indigo-600 hover:text-indigo-700 mt-2">
            {open ? <ChevronDown size={13} /> : <ChevronRight size={13} />}
            {open ? '收起活动记录' : `查看活动记录 · ${steps.length} 步`}
          </button>
        )}
        {open && (
          <div className="mt-2 pt-2 border-t border-gray-100 space-y-1.5 fade-up">
            {steps.map((s, i) => <ActivityRow key={i} step={s} />)}
          </div>
        )}
      </div>
    </div>
  )
}
