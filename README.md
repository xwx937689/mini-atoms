# Mini Atoms — AI 应用生成工作台

一个面向 ROOT 全栈岗位笔试的 Atoms Demo：用自然语言描述需求，由 AI Agent 团队分工协作，生成可直接运行的单文件网页应用，实时预览、持续对话修改、版本管理与回滚，并可将成品一键发布为公开分享链接。

## 产品功能

| 功能 | 说明 |
| --- | --- |
| 多 Agent 协作可视化 | Mike（Team Leader）拆解需求并输出结构化规划，Alex（Engineer）生成代码，Reviewer 校验质量。每一步以身份标签、活动卡、工具卡在对话区实时呈现 |
| 真实 AI 生成 | 调用 DeepSeek（OpenAI 兼容 API），从需求解析 → 结构化规划 → 完整 HTML → 质量门禁 → 自动修复一次 |
| 实时预览 | 生成的完整 HTML 在隔离 iframe 中运行，支持 Preview / Code 切换与代码复制 |
| 对话式修改 | 把当前 HTML、原始需求、近期对话与最新指令一起交给模型，在已有版本上迭代，不推倒重来 |
| 数据持久化 | 服务端 SQLite + 浏览器 localStorage 双写，免费部署环境重启后浏览器侧仍保留项目 |
| 版本管理与回滚 | 每次成功生成/修改自动建版本，一键回滚到任意历史版本 |
| 一键发布（延展能力） | 将当前应用发布为独立公开 URL，无需注册即可访问 |

## 技术架构

```
┌──────────────────────────────────────────────┐
│  React 18 + TypeScript + Vite + Tailwind     │
│  三栏工作台 / 活动卡 / iframe 预览 / 版本栏   │
└────────────────────┬─────────────────────────┘
                     │ REST (JSON)
┌────────────────────▼─────────────────────────┐
│  FastAPI                                      │
│  /api/generate  /api/edit  /api/rollback      │
│  /api/projects  /api/publish  /p/{slug}       │
├──────────────────────────────────────────────┤
│  Agent 编排（单工作流 + 角色化过程暴露）      │
│  planner → builder → reviewer → repair        │
├──────────────────────────────────────────────┤
│  SQLite (projects/messages/versions/published)│
└────────────────────┬─────────────────────────┘
                     │ OpenAI-compatible
              DeepSeek API (key 仅在后端环境变量)
```

**为什么是"单 Agent 工作流 + 多角色呈现"？** Atoms 的体验核心是"看得见的协作过程"，但真正并行编排多个独立 Agent 会带来状态同步、成本与延迟的复杂度。这里把确定性流程（规划→生成→校验→修复）用角色化活动卡显式暴露给用户：体验上对齐 Atoms，工程上保持一个可控的状态机。这是本次的主要技术取舍，也便于在面试中解释。

**为什么生成单文件 HTML？** 让生成结果无需构建、无需依赖安装即可在 iframe 里真实运行，把复杂度留给"生成质量与交互"，而不是环境配置。

## 核心流程

```
用户输入需求
  → Mike 解析需求，输出结构化 JSON 规划
  → Alex 按规划生成完整 HTML（含内联 CSS/JS + 模拟数据）
  → Reviewer 做质量门禁（结构校验 + 模型审查）
  → 不通过则自动修复一次，仍失败则保留上一可用版本
  → 写入 SQLite 并生成版本记录
  → 前端 iframe 实时渲染，对话区展示全过程
  → 用户继续对话修改 / 回滚 / 发布
```

## 目录结构

```
MiniAtoms/
├── backend/
│   ├── app/
│   │   ├── main.py         # FastAPI 路由与 Agent 编排
│   │   ├── llm.py          # LLM 客户端（真实调用 + 结构化解析）
│   │   ├── prompts.py      # 各角色 prompt
│   │   ├── validator.py    # HTML 结构质量门禁
│   │   ├── database.py     # SQLite 持久化
│   │   ├── mock.py         # 无 Key 时的降级演示
│   │   └── config.py       # 环境变量配置
│   ├── requirements.txt
│   └── .env.example
├── frontend/
│   └── src/
│       ├── App.tsx         # 三栏工作台 UI
│       ├── api.ts          # 前端 API 封装与类型
│       ├── storage.ts      # localStorage 持久化镜像
│       └── index.css
└── README.md
```

## 本地启动

**后端**（需要 Python 3.10+）：

```bash
cd backend
pip install -r requirements.txt
cp .env.example .env      # 填入你的 DEEPSEEK_API_KEY
python -m uvicorn app.main:app --host 0.0.0.0 --port 8000
```

**前端**（需要 Node 18+）：

```bash
cd frontend
npm install
npm run dev
```

浏览器打开 `http://localhost:5173`。未配置 API Key 时自动进入演示模式（Mock 生成），功能链路仍可完整体验。

## 环境变量

| 变量 | 说明 |
| --- | --- |
| `LLM_BASE_URL` | OpenAI 兼容 API 地址，默认 `https://api.deepseek.com` |
| `LLM_API_KEY` | API Key，**只在后端**，不会下发到前端 |
| `LLM_MODEL` | 模型名，默认 `deepseek-chat` |
| `CORS_ORIGINS` | 允许的前端来源，逗号分隔 |

## 演示建议

1. 点「新建应用」，选择第一个示例「接口自动化测试质量看板」，发送
2. 观察对话区 Mike 的规划卡与 Alex 的活动步，右侧 iframe 渲染出真实网页
3. 继续输入：「把页面改成深色主题，失败率超过 10% 的服务用红色显示」
4. 再输入：「增加一个失败详情弹窗，展示请求参数和响应内容」
5. 点「版本」，回滚到 V1，再点「发布」，用公开链接打开

## 已知限制

- 生成目标限定为单文件 HTML，不生成需要构建工具或多文件的项目
- 生成的模拟数据为前端内置，不连接真实后端数据源
- 免费部署层重启后服务端 SQLite 可能重置，浏览器 localStorage 作为兜底保留最近项目
- 无用户系统与权限（题目明确无需注册）

## 后续规划（优先级）

1. P0：把生成结果落到对象存储 + 托管数据库，摆脱免费层磁盘限制
2. P1：流式输出生成过程，缩短首字节等待
3. P1：可视化元素选中编辑（对齐 Atoms Visual Editor）
4. P2：Race Mode — 同一需求多模型并排对比
