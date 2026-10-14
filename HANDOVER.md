# TeamAI Issue Hub 交接文档

> 文档日期：2026-10-14
> 当前版本：内部 MVP

## 1. 项目目标

TeamAI Issue Hub 是 TeamAI 团队上下文工具中的“问题与经验”能力。AI Capture Skill + MCP 是主要随手记录入口，Web 用于查看、审核和整理；本仓库的 `teamai-cli` 是可选 CLI 客户端，不是使用 Skill 的前置依赖。

TeamAI API 是统一适配层。Compose 团队部署使用 PostgreSQL；SQLite 仅用于本地开发和既有数据迁移来源。配置 GitLab CE 后，可把原始问题同步为 GitLab Issue，团队知识条目仍由 Issue Hub 管理。

Issue Hub 先作为第一版共享团队知识库，GitLab CE 可选同步原始 Issue；单独的已审核知识条目保留源 Issue 关联。当前产品已经包含以下闭环：

- 快速录入：记录问题，填写所属项目和所属类型（算法调研、算法预研、模型部署、组件开发、项目支撑、工作日常）。
- 总览：总记录数、待处理、处理中、已解决、近期记录。
- 检索：按关键词搜索，并按状态过滤；列表和详情展示所属项目、所属类型。
- 详情：查看完整内容、元数据、外部 GitLab 链接和同步状态。
- 生命周期：追加解决方案、解决问题、重新打开问题。
- 团队知识沉淀：解决 Issue 必须补充验证证据并进入待审核队列；人工整理、确认后生成独立知识条目，记录项目、技术栈、审核人标识和来源 Issue。重新打开源 Issue 会将知识标记为待复核并从知识搜索中隐藏。
- 去重与脱敏：内容哈希去重，自动遮盖 API Key、Token、密码、GitHub Token、AWS Access Key 等常见凭证。
- 多入口：Web、CLI、MCP 共享同一套 API。
- Skill 先搜索既有经验，再建议记录；不把未验证方案当作已解决问题，也不会未经用户明确同意直接发布团队知识。

## 2. 当前状态

### 已完成

- FastAPI 后端；Compose 默认运行 PostgreSQL，SQLite 开发模式仍可用。
- SQLite 到 PostgreSQL 的显式迁移工具 `teamai_migrate.py`，源数据库保持只读。
- PostgreSQL 数据访问层与 Compose 私有数据库服务；schema DDL、兼容列补齐、历史回填、索引和版本记录均在事务级 advisory lock 下作为单一版本迁移应用；已迁移数据库启动时只校验结构、不隐式修改 schema；遇到更高版本或迁移记录与真实结构不一致会拒绝启动，避免错误版本运行。GitLab CI 添加独立 PostgreSQL 集成测试任务，当前尚未由 Runner 执行。
- Web 快速录入页和问题总览页。
- Web 采用 ChatGPT 风格的暖白/浅灰底色、蓝色主操作和中性灰层级；按钮、链接和弱化文字通过 WCAG AA 对比度检查；主要移动触控控件至少 44px，状态筛选按钮暴露 `aria-pressed` 状态。
- Web 记录库支持分页加载、过滤、GitLab 状态/外链、重新打开；包含移动布局及空、错误状态反馈。
- GitLab 有待同步记录时，Web 记录库显示手动重试按钮，并反馈成功、失败及剩余数量。
- 配置 GitLab 时后台每 60 秒自动重试最多 10 条待同步记录；可用 `SYNC_INTERVAL_SECONDS=0` 关闭。
- `/v1/search` 支持 `q`、`kind`、`source`、`status`、分页参数；关键词中的 `%`、`_` 和反斜杠按字面量搜索，同更新时间时按记录 ID 稳定排序。
- `/v1/overview` 提供总览统计。
- 记录详情、追加方案、解决、重新打开。
- Web 录入和管理仅要求填写所属项目、所属类型，不再显示技术栈、环境、复现步骤、来源和标签字段；已存在的旧元数据在存储/API 层保留以兼容历史数据。五类所属类型由 schema migration version 4 新增的 `work_type` 字段保存，旧记录保留为空并显示“未分类”，不会自动猜测分类。
- Web 管理台已拆分总览、问题、知识审核、知识库和同步管理；问题支持表格/看板、组合过滤和浏览器本地保存视图。详情展示创建、编辑、方案、解决/重开、知识发布及 GitLab 同步活动时间线。
- PostgreSQL schema migration version 2 新增 record_events；version 4 新增所属类型；SQLite 启动也会兼容创建活动表及所属类型字段。旧记录只补“历史记录”标记，不虚构迁移前的具体活动，也不改写旧分类。
- `teamai_cli.py`：可选 CLI 客户端；支持 `note`、`issue`、`capture`、`search`、带验证证据的 `resolve`，及 `knowledge candidates/publish` 审核流程。
- `mcp_server.py`：AI Skill 使用的 MCP 工具，支持按类型/状态/来源过滤并分页搜索、创建、追加方案、带验证证据解决、审核候选和经明确批准发布知识。
- CLI/MCP 共用 `teamai_transport.py`：拒绝带凭据、非法端口或不安全字符的 API 配置，禁止 HTTP 重定向转发 API Key，并避免把 URL、响应正文或底层异常泄漏到错误消息。
- 共享 Skill：`skills/teamai-capture/SKILL.md`，说明先搜索、记录所需元数据、验证要求和人工审核发布边界。
- GitLab CE 可选同步、失败本地保留、`POST /v1/sync` 补偿同步。
- API Key 鉴权、健康检查、请求 ID、安全响应头、生产配置启动校验及结构化请求日志。
- Dockerfile、Docker Compose、生产检查清单。
- GitLab CI 为 Python 3.10、3.11、3.13 执行回归/编译检查，执行前端 JavaScript 语法与交互测试，并在 Python 3.11 + Chromium 执行浏览器烟测（流水线尚未在实际 Runner 上运行）。

### 尚未完成

- 公司 OIDC/LDAP 尚未在应用内实现，目前通过 API Key 和反向代理预留接入边界。
- Docker/Podman 在开发机上未安装，镜像构建、Compose 验证和命名卷权限尚未验证。
- 尚未使用真实 GitLab CE 地址、项目 ID 和 Token 做联调。
- PostgreSQL Compose 路径已配置，但本机无可用 Docker/测试库，尚需 CI 或部署环境执行真实 PostgreSQL 集成测试和 Compose 启动验证。
- 生产 PostgreSQL 的自动定时备份及异机恢复演练仍需在部署环境落实；`teamai_backup.py` 只适用于本地 SQLite。
- 当前临时策略为记录和备份永久保留；没有应用内删除/清理接口或自动清理任务。若未来变更策略，应同时处理主库、本机安全副本和异机备份。

## 3. 架构和数据流

```text
Claude Code / Qwen Code -- Capture Skill + MCP --┐
Web 审核台 --------------------------------------┼--> TeamAI Issue Hub API --> PostgreSQL（Compose）
可选 teamai-cli 客户端 ---------------------------┘         |                  ├─ 问题 / 随记
                                                         |                  └─ 审核后的团队知识
                                                         └--> 可选 GitLab CE（仅原始 Issue）

本地开发可用 SQLite；通过 `teamai_migrate.py` 迁移旧库到 PostgreSQL。
```

设计原则：Skill 是 AI 记录入口，MCP 提供工具调用，CLI 仅为可选界面；所有入口共用 TeamAI API。API 负责脱敏、去重、审核状态和关系管理，并可将原始 Issue 同步到 GitLab。团队知识条目需要验证证据和人工整理/发布，且与源 Issue 分开保存。

主要数据表由 `app/main.py` 的 `init_db()` 初始化。核心字段包括记录类型、状态、来源、作者、会话 ID、内容哈希、外部系统、外部 ID、外部 URL、同步错误和时间戳。

## 4. 目录说明

| 路径 | 用途 |
| --- | --- |
| `app/main.py` | FastAPI 路由、SQLite、分类、脱敏、GitLab 适配和同步 |
| `web/index.html` | Web 页面结构 |
| `web/app.js` | 录入、总览、搜索、过滤、详情和生命周期操作 |
| `web/detail-focus.js` | 详情视图的焦点进入、关闭与返回管理 |
| `web/safe-external-url.js` | 限制 GitLab 外链为无凭据的 HTTP(S) URL |
| `web/load-more.js` | 分页加载按钮的进度、成功和可重试失败状态 |
| `web/request-errors.js` | 将 FastAPI 校验错误转换为清晰的中文字段提示 |
| `web/styles.css` | Web 样式 |
| `teamai_cli.py` | 命令行入口 |
| `mcp_server.py` | stdio MCP Server |
| `teamai_transport.py` | CLI/MCP 共用的安全 HTTP 客户端 |
| `teamai_backup.py` | SQLite 在线备份、完整性验证和停止服务后的安全恢复 |
| `teamai_postgres_backup.py` | 流式导出并验证 PostgreSQL 自定义格式备份到宿主机 |
| `teamai_postgres_restore.py` | 检查应用服务已停止、拒绝空/无效归档，再经 Docker Compose 单事务流式恢复 PostgreSQL |
| `teamai_migrate.py` | 将 SQLite 记录迁移至 `DATABASE_URL` 指定的 PostgreSQL 数据库 |
| `requirements-dev.txt` | 本地测试依赖；生产镜像只安装 `requirements.txt` |
| `skills/teamai-capture/SKILL.md` | Claude Code/Qwen Code Skill：先搜索、随手记录、验证、人工审核和发布边界 |
| `integrations/mcp-config.example.json` | MCP 配置示例 |
| `tests/test_api.py` | API 回归测试 |
| `tests/test_postgres.py` | PostgreSQL 闭环集成测试（通过 `TEST_DATABASE_URL` 启用） |
| `tests/test_postgres_backup.py` / `tests/test_postgres_restore.py` | PostgreSQL Docker 流式备份/恢复错误处理与不覆盖策略 |
| `tests/test_clients.py` | CLI/MCP 共用传输层安全与 HTTP 测试 |
| `tests/web/` | 详情焦点管理和 GitLab 手动重试交互的 Node 原生测试 |
| `scripts/browser_smoke.py` | 使用临时 SQLite 数据库执行 Playwright Chromium 桌面/手机烟测 |
| `PRODUCTION.md` | 部署前检查清单 |
| `LOCAL_DEV.md` | Windows 本机 PostgreSQL 开发实例、管理页面及测试启动/停止说明 |
| `ARCHITECTURE.html` | 产品边界、组件关系与数据流的图示说明 |
| `.env.example` | 环境变量模板 |
| `Dockerfile` / `docker-compose.yml` | 容器部署文件 |

## 5. 本地运行

### 启动 API

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements-dev.txt
python -m uvicorn app.main:app --host 127.0.0.1 --port 8787
```

打开 <http://127.0.0.1:8787/>。

当前开发环境已验证的服务地址也是 `http://127.0.0.1:8787/`。若端口被占用，换端口后同步设置 `TEAMAI_API`。

### CLI 示例

```powershell
python teamai_cli.py note "记录一条经验"
python teamai_cli.py issue "记录一个待解决问题"
python teamai_cli.py capture "记录一次构建失败经验" --type auto
python teamai_cli.py search "Docker 构建失败"
python teamai_cli.py resolve 1 --solution "补充配置并重启" --verification "连续重启三次后首次连接均成功"
python teamai_cli.py knowledge candidates
python teamai_cli.py knowledge publish 1 --title "MCP 首次连接失败处理" --content "..." --reviewer "team-account"
```

默认 API 地址是 `http://127.0.0.1:8787`。可通过 `TEAMAI_API` 修改；配置鉴权时同时设置 `TEAMAI_API_KEY`。

### MCP Server

```powershell
python mcp_server.py
```

常用工具包括：`teamai_create_note`、`teamai_create_issue`、`teamai_search`、`teamai_get_issue`、`teamai_append_solution`、`teamai_resolve`（必须给出 verification）、`teamai_knowledge_candidates`、`teamai_publish_knowledge`。最后一个工具只在用户明确批准已展示的文章后调用。客户端配置参考 `integrations/mcp-config.example.json`。

## 6. API 入口

| 方法 | 路径 | 说明 |
| --- | --- | --- |
| `POST` | `/v1/notes` | 创建随笔 |
| `POST` | `/v1/issues` | 创建问题 |
| `POST` | `/v1/solutions` | 创建解决方案 |
| `POST` | `/v1/classify` | 自动判断记录类型 |
| `GET` | `/v1/overview` | 问题总览统计 |
| `GET` | `/v1/search` | 关键词搜索和过滤 |
| `GET` | `/v1/issues/{id}` | 查看记录详情 |
| `POST` | `/v1/issues/{id}/append-solution` | 追加解决方案 |
| `POST` | `/v1/issues/{id}/resolve` | 带验证证据标记已解决，并加入知识审核队列 |
| `PUT` | `/v1/records/{id}` | 编辑普通记录或未解决问题的内容和元数据；已解决问题与知识条目受保护 |
| `POST` | `/v1/issues/{id}/reopen` | 重新打开 |
| `GET` | `/v1/knowledge/candidates` | 列出待审核/复核的已解决问题 |
| `POST` | `/v1/issues/{id}/publish-knowledge` | 经人工批准发布独立知识条目，保留来源关联 |
| `POST` | `/v1/sync` | 补偿同步 GitLab |
| `GET` | `/health` | 服务和待同步状态 |

启用 `TEAMAI_API_KEY` 后，所有 `/v1/*` 请求都要带：

```text
X-TeamAI-Key: <TEAMAI_API_KEY>
```

## 7. 环境变量和 GitLab

从 `.env.example` 复制配置。主要变量：

```env
APP_DB=teamai.db
DATABASE_URL=postgresql://teamai:password@localhost:5432/teamai
APP_HOST=127.0.0.1
APP_PORT=8787
TEAMAI_API_KEY=
TEAMAI_REVIEWER_ID_HEADER=X-Authenticated-User
TEAMAI_REVIEWER_GROUPS_HEADER=X-Authenticated-Groups
TEAMAI_REVIEWER_REQUIRED_GROUP=teamai-approvers
GITLAB_URL=
GITLAB_TOKEN=
GITLAB_PROJECT_ID=
```

只有同时设置 `GITLAB_URL`、`GITLAB_TOKEN`、`GITLAB_PROJECT_ID` 时才启用 GitLab 同步。未配置时系统运行在本地模式，功能仍可完整使用。
本地开发不设置 `DATABASE_URL` 时使用 `APP_DB` SQLite 文件；Compose 生产部署通过 `DATABASE_URL` 连接 PostgreSQL。生产启动还要求配置可信代理身份头、组头和审核组。代理必须覆盖客户端传入的同名头，且 API 端口仅允许代理访问。

## 8. 接手后的优先级

### P0：修复同步一致性（已完成）

- [x] 补偿同步按 `external_id` 分流：已有 Issue 只发 PUT，没有外部 ID 才发 POST。
- [x] 本地记录先持久化；同步任务使用 60 秒 SQLite 租约，防止多个请求并发创建同一记录。
- [x] GitLab Issue 加入稳定 `teamai-sync-<hash>` 标签。POST 响应丢失时，重试先按标签查找并关联已有 Issue。
- [x] 生命周期更新统一调用 `update_gitlab()`；GitLab 失败时保留本地更改并写入 `sync_error`。
- [x] resolved 执行 close，triage/reopen 执行 reopen。
- [x] GitLab 同步更新 status 标签，避免 Issue 状态标签与本地生命周期状态脱节。
- [x] 回归测试覆盖 PUT/POST 选择、本地失败保留和重开动作。
- [x] 本机 HTTP mock 覆盖 GitLab URL 编码、PRIVATE-TOKEN 请求头、POST JSON 负载及 HTTP 错误脱敏；仍需真实 GitLab 联调。

### P1：生产可用性

- [x] SQLite 连接增加 `timeout`、`busy_timeout`、`foreign_keys` 和 WAL/NORMAL 同步配置，并在请求后关闭连接。
- [x] 启动时自动创建 `APP_DB` 父目录。
- [x] `/health` 不直接暴露数据库绝对路径。
- [x] 内容哈希冲突返回 409，并保证本地记录不会部分更新。
- [x] 创建记录时在 `BEGIN IMMEDIATE` 事务内完成查重和插入；旧数据库因历史重复值无法建立唯一哈希索引时，并发写入仍只会新增一条记录。
- [x] Issue 追加方案和状态更新在 SQLite `BEGIN IMMEDIATE` 写事务内读取并提交，避免并发更新覆盖彼此内容。
- [x] 详情关闭后若异步列表刷新替换了聚焦的记录按钮，列表会恢复同一条记录的焦点；过滤掉记录时回到搜索框。
- [x] 凭证脱敏覆盖内容、标题、环境/复现、标签、来源、作者/会话标识及解决记录中的来源文本。
- [x] 启动时拒绝不完整/无效端口的 GitLab 配置、不安全 HTTP Token/API Key；CLI/MCP API URL 校验端口；生产模式拒绝弱 API Key。
- [x] 请求日志带安全校验的 Request ID、耗时和状态码；意外错误仅返回通用消息，并给错误响应加安全头。
- [x] GitLab 暂时不可用时后台每 60 秒重试最多 10 条失败记录；手动同步仍可用，关闭值为 `SYNC_INTERVAL_SECONDS=0`。
- [x] Compose 用 PostgreSQL 作为团队部署主库，数据库不映射宿主端口；生产配置校验 PostgreSQL URL 和数据库驱动。
- [x] 将 SQLite 与 PostgreSQL 的最近记录时间筛选统一为 UTC 时间戳比较。
- [ ] 在部署机或 CI 验证 Docker build、Compose 启动和数据卷持久化。
- 接入公司 OIDC/LDAP 反向代理，并确认用户身份映射、访问审计和权限边界。

### P2：规模和体验

- 使用真实 GitLab CE 做端到端联调。
- [x] 提供 SQLite 本地在线备份、完整性校验和安全恢复；Compose PostgreSQL 改用 `pg_dump`/`pg_restore`，但仍需部署环境配置定时任务、异机存储和恢复演练。
- 配置日志采集和告警。
- 当前按用户选择暂时永久保留记录和备份，不启用自动擦除或备份清理；未来若调整策略，再设计覆盖主库、安全副本和异机备份的可审计清理流程。
- PostgreSQL 集成测试需在 GitLab Runner/测试数据库执行；当前本机没有可用的无凭证 PostgreSQL 测试连接。
- 多实例部署前增加任务队列或可靠的同步 worker，并验证 PostgreSQL 多实例负载和恢复流程。
- [x] 已解决 Issue 经团队人工审核后沉淀为独立知识条目；搜索默认只展示已审核知识，重开原问题会隐藏知识并要求复核。
- [x] 增加生产审核身份边界：生产启动要求配置可信代理身份头、组头和审核组；发布时校验代理身份/审核组，审核人姓名不再由客户端自报。仍需在部署时把代理配置为覆盖客户端同名头，并限制后端只可由代理访问。
- [ ] 若团队上下文工具已有独立知识存储，再设计该库的正式发布/同步接口与失败补偿；当前 Issue Hub PostgreSQL 是第一版知识存储，本仓库未发现外部上下文库接口。

## 9. 验证方式

当前已验证：

- Python 3.13.5 + SQLite：74 passed, 9 skipped；配置本机 PostgreSQL 18.6 `TEST_DATABASE_URL` 后：83 passed, 0 skipped。
- Node.js 22：20 个交互用例通过（详情焦点管理 4 个、外链协议校验 2 个、加载更多 3 个、请求错误提示 3 个、无障碍/色彩对比检查 6 个、GitLab 重试 2 个）。

```powershell
python -m pytest -q
# 74 passed, 9 skipped without PostgreSQL integration

python -m py_compile app\main.py teamai_cli.py mcp_server.py teamai_backup.py teamai_migrate.py teamai_transport.py teamai_postgres_backup.py teamai_postgres_restore.py
node --check web\app.js
node --check web\detail-focus.js
node --check web\safe-external-url.js
node --check web\load-more.js
node --check web\request-errors.js
node --check web\sync-retry.js
node --test tests\web\*.test.js
# 20 passed
```

Playwright Chromium 浏览器烟测使用临时隔离数据库，可重复执行：

```powershell
pip install -r requirements-e2e.txt
python -m playwright install chromium
python scripts\browser_smoke.py
# 可选：python scripts\browser_smoke.py --screenshots-dir "$env:TEMP\teamai-browser-smoke"
```

已实测创建问题、打开详情并将焦点移到标题、解决/重开、Escape 关闭后返回同一记录，以及延迟列表刷新期间关闭详情后仍保持焦点；1440px 桌面和 390px 手机视口均无横向溢出，控制台无未捕获脚本错误。当前 Chromium 148.0.7778.96 烟测通过，覆盖管理页编辑与知识状态。截图可选择保存到指定目录。Docker 构建和真实 GitLab 联调仍未验证。
已添加 `.gitlab-ci.yml`，在 GitLab Runner 上运行 Python 检查、全部前端脚本语法检查和 Node 交互测试，并安装 Playwright Chromium 执行隔离浏览器烟测；当前尚无 Runner 执行结果。

手工验证清单：

1. 打开首页，确认总览数字、记录列表和搜索框正常。
2. 使用类型、来源、状态过滤，打开一条记录详情。
3. 追加方案，填写验证证据后解决；确认候选进入审核队列，发布文章后验证搜索结果和来源 Issue 关联。
4. 通过 CLI 创建一条记录，并在 Web 搜索到它。
5. 启动 MCP Server，确认搜索和创建工具可调用。
6. 配置 API Key 后，确认无 `X-TeamAI-Key` 的 `/v1/*` 请求返回 401。
7. GitLab 不可用时确认记录仍保存，并能从 `/v1/sync` 重试。

Docker 尚未在当前机器验证，接手者应在有 Docker 的环境执行：

```powershell
docker compose config
docker compose build
docker compose up -d
```

## 10. 运维注意事项

- `teamai.db` 是运行数据，不要把生产数据库提交到代码仓库；开发目录中的数据库文件仅用于本地验证。
- 备份 SQLite 前应停止服务，或使用一致性快照。
- `TEAMAI_API_KEY`、`GITLAB_TOKEN` 都属于敏感凭证，不要写入日志、Issue 内容或提交记录。
- 服务对团队开放前必须使用 HTTPS 和公司网络访问控制，并设置持久化数据卷。
- 监控 `/health` 的 `pending_sync`；持续非零通常表示 GitLab 配置、网络或权限异常。

## 11. 交接结论

当前版本已经具备“录入 -> 总览/搜索/过滤 -> 查看详情 -> 追加方案 -> 解决/重开”的内部 MVP 闭环。GitLab 补偿同步重复创建 Issue 的 P0 已通过稳定标签和重试对账处理，并有回归测试；接手后应优先在真实 GitLab CE 上验证该行为，再完成 Docker、统一登录和异机备份恢复演练。
