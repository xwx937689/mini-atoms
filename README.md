# Mini Atoms — AI 应用生成工作台

用一句自然语言描述需求，AI 团队分工完成规划、编码、质量检查，产出可直接运行的单文件网页应用。支持持续对话修改、版本回滚、发布分享。

**这个项目的重点不是"让模型生成出东西"，而是判断生成出来的东西到底能不能用。** 三层质量门禁（静态结构 / 模型审查 / 真实浏览器验证）任一失败即拒绝入库，保留上一版本。

## 产品功能

| 功能 | 说明 |
| --- | --- |
| 多角色 Agent 过程呈现 | Mike（规划）输出结构化 JSON 规划，Alex（生成）产出代码，Reviewer 逐项校验。以身份标签、活动卡、工具记录在对话区呈现。**执行是确定性单工作流，不是多个 Agent 并行** |
| 真实 AI 生成 | 调用 DeepSeek（OpenAI 兼容 API），从需求解析 → 结构化规划 → 完整 HTML → 质量门禁 → 自动修复一次 |
| 实时预览 | 生成的完整 HTML 在隔离 iframe 中运行，支持 Preview / Code 切换与代码复制 |
| 对话式修改 | 把当前 HTML、原始需求、近期对话与最新指令一起交给模型，在已有版本上迭代，不推倒重来 |
| 数据持久化 | 服务端 SQLite + 浏览器 localStorage 双写，免费部署环境重启后浏览器侧仍保留项目 |
| 版本管理与回滚 | 每次成功生成/修改自动建版本，一键回滚到任意历史版本 |
| 一键发布（延展能力） | 将当前应用发布为独立公开 URL，无需注册即可访问；发布页用 sandbox iframe + CSP 隔离 |
| 质量评分 | 5 维加权评分（需求覆盖/交互/数据/视觉/健壮性），展示在对话区与顶部 |

## 质量验收标准

平台对"生成的应用"有两层验收，避免"能跑就算过"：

### L1 硬门禁（决定是否可用）

**每一轮都完整执行三项检查**，不是"过了一项就往下走"：

| 检查 | 内容 |
|---|---|
| 结构校验 | doctype / html / head / body / 闭合标签 / 无 Markdown 围栏 / 无占位符 |
| 模型审查 | 模型判断是否有明显缺陷；返回非法 JSON 或缺 `valid` 字段即视为审查异常 |
| 浏览器冒烟 | 页面渲染、正文内容量、可见交互控件、**真实点击可用**、JS 运行时错误（本地启用） |

```
第1轮：三项全跑 → 有失败 → 模型修复一次 → 第2轮：三项全跑（完整复验）
                        ↓ 仍失败
                   拒绝保存，旧版本保留
```

**硬规则（与代码一致，非宣传语）**：

- **模型审查异常（status=error）→ 阻断保存**，绝不当作通过。先自动重试一次以规避偶发抖动。
- 浏览器检查 **skipped 不等于 passed**：结果标记 `passed_with_skips`，活动记录显示 "SKIPPED（非通过）"。
- 修复后的结果**必须重跑全部三项**，不存在"修复即通过"。
- 生成、修改、Mock 降级三条路径**共用同一门禁**，无绕过分支。
- Mock 模式用真实结构校验（非恒真），结果标记 `review_mode=mock`、`verified=false`，前端不当成真实模型审查通过。
- 浏览器"点击可用"是**真点击**：Playwright 的 `click()` 在事件处理器抛错时也会正常返回，所以额外检查点击后是否新增了 `pageerror`。有按钮但点不动 → 不合格；页面本来就没有按钮（纯表单类）→ 不因此扣分。
- 控制台错误只统计真正的运行时异常（`Uncaught` / `TypeError` / `ReferenceError` / `SyntaxError` 等），favicon 404、外部资源被拦截等噪声不计入。
- 浏览器 skipped 时活动记录显示"质量校验完成（部分检查未执行）"，**不会**说"全部检查项已执行"。

### 输出截断防护

模型单次输出有 token 上限，复杂需求会把 HTML 截断在 JS 中间。处理：

- 检测截断（未闭合 `<script>`/`<style>`、缺 `</html>`、JS 花括号不配对）→ 自动重试并要求压缩范围
- **重试后仍然截断 → 直接抛异常拒绝保存**，绝不靠 `clean_html` 补上 `</script>` 就当作完成。补标签能修格式，救不回被截断的业务逻辑（搜索、导出可能只写了一半）
- 生成结果超 15,000 字符 → 主动重写一版更紧凑的（保留全部功能），避免后续修改必然截断
- 修改后比原版缩水超 35% → 判定功能丢失，拒绝并保留旧版

### 自动化测试

```bash
cd backend
pip install -r requirements.txt -r requirements-dev.txt
python -m pytest -v          # 35 个用例，无需 API Key / 网络
```

覆盖：质量门禁各分支（通过 / 不通过 / 审查异常 / 浏览器失败 / skipped）、修复后完整复验、
Mock 模式不谎报、**接口级失败不写库**、**事务原子性（写一半失败整体回滚）**、截断检测与拒绝、
发布页 CSP 与 sandbox、输入长度与限流。浏览器相关用例在无 Chromium 环境下自动 skip（不伪造通过）。

### L2 软评分（衡量好不好）

| 维度 | 权重 | 衡量方式 |
|---|---|---|
| 需求覆盖 | 25% | 规划的功能项有多少在代码中真的出现 |
| 交互实现 | 25% | 事件绑定数 / 可见控件数的比例 |
| 数据真实感 | 20% | 是否有结构化数据集，而非玩具占位符 |
| 视觉完整 | 20% | 样式广度、图表元素、响应式、配色系统 |
| 健壮性 | 10% | 无浏览器存储 API、无 alert、无外部依赖 |

输出 0-100 总分 + A/B/C/D 等级 + 各维度明细。这是**启发式静态评估**（可复现、不依赖模型心情），不是模型自评。

### 修改的额外防护

- 修改后 HTML 若比原版缩水超过 35%（原版 ≥1500 字符），判定为"丢失既有功能"并拒绝
- 修改失败时保留上一可用版本，不产生新版本记录

## 技术架构

```
React 18 + TS + Vite + Tailwind     ← 工作台 / 活动卡 / iframe 预览 / 版本栏
        │ REST
FastAPI                              ← /api/generate /api/edit /api/rollback
        │                              /api/projects /api/publish /p/{slug}
Agent 编排（确定性单工作流）
        planner → builder → quality_gate(static → review → browser) → repair
        │
SQLite（projects / messages / versions / published）· 单事务写入
        │
DeepSeek API（OpenAI 兼容，Key 仅存在于后端环境变量）
```

**为什么是"单工作流 + 多角色呈现"？** Atoms 的体验核心是"看得见的协作过程"，但真正并行编排多个独立 Agent 会带来状态同步、成本与延迟的复杂度。这里把确定性流程用角色化活动卡显式暴露：体验上对齐 Atoms，工程上保持一个可控的状态机。**不声称多 Agent 并行协作**——这是主动取舍。

**为什么生成单文件 HTML？** 生成结果无需构建、无需装依赖即可在 iframe 里真实运行，把复杂度留给"生成质量与交互"而不是环境配置。代价是单文件有体积上限、复杂需求易触发输出截断，已用截断检测 + 重试 + 硬拒绝处理。

## 核心流程

```
用户输入需求
  → Mike 解析需求，输出结构化 JSON 规划
  → Alex 按规划生成完整 HTML（内联 CSS/JS + 模拟数据）
  → 质量门禁第 1 轮：静态结构 + 模型审查 + 浏览器真跑
  → 有失败 → 模型修复一次 → 第 2 轮：三项全部重新跑
  → 仍失败 → 拒绝保存，上一可用版本自动保留
  → 通过 → 单事务写入（HTML 与版本记录同时生效）
  → 前端 iframe 实时渲染，对话区展示全过程
  → 用户继续对话修改 / 回滚 / 发布
```

## 目录结构

```
MiniAtoms/
├── backend/
│   ├── app/
│   │   ├── main.py          # 路由 · 请求校验 · 质量门禁编排 · 发布页
│   │   ├── llm.py           # 模型调用 · 截断检测 · 审查三态返回
│   │   ├── prompts.py       # 四个角色的提示词
│   │   ├── validator.py     # 静态结构校验
│   │   ├── scoring.py       # 5 维质量评分
│   │   ├── browser_check.py # Playwright 冒烟检查（可选）
│   │   ├── database.py      # SQLite · 单事务保存
│   │   ├── ratelimit.py     # IP 滑动窗口限流
│   │   ├── mock.py          # 无 Key / 模型不可用时降级
│   │   └── config.py        # 环境变量
│   ├── tests/               # 35 个 pytest 用例（无需 Key / 网络）
│   ├── requirements.txt
│   ├── requirements-dev.txt
│   └── .env.example
├── frontend/src/
│   ├── App.tsx              # 三栏工作台
│   ├── api.ts               # API 封装与类型 · 预览沙箱 shim
│   └── storage.ts           # 浏览器侧镜像（兜底）
└── render.yaml
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

**运行测试**（不需要 API Key，也不需要网络）：

```bash
cd backend
pip install -r requirements-dev.txt
python -m pytest -v
```

**启用浏览器验证**（可选，本地）：

```bash
pip install playwright && python -m playwright install chromium
```

未安装时浏览器检查会标记为 `skipped`（不记为通过），不影响其余流程。

## 环境变量

| 变量 | 说明 |
| --- | --- |
| `LLM_BASE_URL` | OpenAI 兼容 API 地址，默认 `https://api.deepseek.com` |
| `LLM_API_KEY` | API Key，**只在后端**，不会下发到前端 |
| `LLM_MODEL` | 模型名，默认 `deepseek-chat` |
| `CORS_ORIGINS` | 允许的前端来源，逗号分隔 |
| `CORS_ALLOW_ALL` | 设为 `true` 才允许任意来源，**默认 false** |
| `RL_GENERATE` | 每个 IP 在窗口内允许的生成/修改次数，默认 6 |
| `RL_WINDOW` | 限流窗口秒数，默认 600 |
| `BROWSER_CHECK` | 设为 `false` 关闭浏览器验证，默认 `auto` |
| `TRUST_PROXY_HEADERS` | 是否信任 `X-Forwarded-For`，默认 true（Render 场景） |
| `LLM_TIMEOUT` | 模型请求超时秒数，默认 180 |

## 使用流程

1. 新建应用 → 输入需求 → 对话区展示规划卡与逐条活动记录
2. 展开「生成质量评分」查看五维明细
3. 继续追加修改指令（如「加个搜索框」）→ 新版本生成
4. 版本面板可回滚到任意历史版本
5. 「发布」生成公开分享链接

> 未配置有效 API Key 时顶部会显示「演示模式」标签，此时为内置示例数据，非真实生成。

## 已知限制

| 限制 | 说明 |
|---|---|
| 单文件 HTML | 不生成需要构建工具或多文件的项目；单文件有体积上限 |
| 模拟数据 | 数据内置在前端 JS，不连接真实后端 |
| 浏览器验证仅本地 | Render 免费层 512MB 跑不动 Chromium，线上自动 `skipped`，**不记为通过** |
| 冒烟级验证 | 只能证明"不崩、有交互"，不能证明业务逻辑正确 |
| 限流非分布式 | 进程内内存计数，多实例部署失效，需 Redis 或边缘 WAF |
| 截断检测是启发式 | 花括号计数，非 JS AST 解析；误判代价是多重试一次 |
| 发布页同域 | 靠 sandbox + CSP 等效隔离，正式公开服务建议独立域名 |
| 无用户系统 | 题目明确无需注册；也因此无法做按用户配额 |
| 免费层磁盘 | 容器重启可能重置 SQLite，浏览器镜像作为兜底 |

## 后续规划（按价值排序）

1. **浏览器验证升级为断言** — 现在只证明不崩；可针对规划里的 features 自动生成断言，验证筛选/导出的真实效果
2. **发布页独立域名** — 彻底隔离，不依赖 sandbox 等效
3. **对象存储 + 托管数据库** — 摆脱免费层磁盘限制
4. **限流换 Redis** — 支持多实例
5. **流式输出** — SSE 逐阶段推送，缩短首字节等待
6. **可视化元素点选编辑** — 对齐 Atoms Visual Editor
