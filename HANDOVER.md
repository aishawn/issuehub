# TeamAI Issue Hub 交接文档

> 文档日期：2026-10-14
> 当前版本：内部 MVP

## 1. 项目目标

TeamAI Issue Hub 用于收集团队在 Claude Code、Qwen Code、`teamai-cli` 和 Web 页面中遇到的经验、问题与解决方案，并提供统一的查看、搜索、过滤和问题总览。

第一阶段采用本地 TeamAI API 作为统一适配层，SQLite 保存元数据；配置 GitLab CE 后，可以把记录同步为 GitLab Issue，继续使用 GitLab 的评论、权限和协作能力。

当前产品已经包含以下基础闭环：

- 快速录入：随笔、问题、解决方案三种类型。
- 总览：总记录数、待处理、处理中、已解决、近期记录。
- 检索：按关键词搜索，并按类型、来源、状态过滤。
- 详情：查看完整内容、元数据、外部 GitLab 链接和同步状态。
- 生命周期：追加解决方案、解决问题、重新打开问题。
- 去重与脱敏：内容哈希去重，自动遮盖 API Key、Token、密码、GitHub Token、AWS Access Key 等常见凭证。
- 多入口：Web、CLI、MCP 共享同一套 API。

## 2. 当前状态

### 已完成

- FastAPI + SQLite 后端。
- Web 快速录入页和问题总览页。
- Web 采用 ChatGPT 风格的暖白/浅灰底色、蓝色主操作和中性灰层级；按钮、链接和弱化文字通过 WCAG AA 对比度检查；主要移动触控控件至少 44px，状态筛选按钮暴露 `aria-pressed` 状态。
- Web 记录库支持分页加载、过滤、GitLab 状态/外链、重新打开；包含移动布局及空、错误状态反馈。
- GitLab 有待同步记录时，Web 记录库显示手动重试按钮，并反馈成功、失败及剩余数量。
- 配置 GitLab 时后台每 60 秒自动重试最多 10 条待同步记录；可用 `SYNC_INTERVAL_SECONDS=0` 关闭。
- `/v1/search` 支持 `q`、`kind`、`source`、`status`、分页参数；关键词中的 `%`、`_` 和反斜杠按字面量搜索，同更新时间时按记录 ID 稳定排序。
- `/v1/overview` 提供总览统计。
- 记录详情、追加方案、解决、重新打开。
- `teamai_cli.py`：`note`、`issue`、`capture`、`search`、`resolve`。
- `mcp_server.py`：创建记录、搜索、读取、追加方案、解决问题。
- CLI/MCP 共用 `teamai_transport.py`：拒绝带凭据、非法端口或不安全字符的 API 配置，禁止 HTTP 重定向转发 API Key，并避免把 URL、响应正文或底层异常泄漏到错误消息。
- 共享 Skill：`skills/teamai-capture/SKILL.md`。
- GitLab CE 可选同步、失败本地保留、`POST /v1/sync` 补偿同步。
- API Key 鉴权、健康检查、请求 ID、安全响应头、生产配置启动校验及结构化请求日志。
- Dockerfile、Docker Compose、生产检查清单。
- GitLab CI 为 Python 3.10、3.11、3.13 执行回归/编译检查，执行前端 JavaScript 语法与交互测试，并在 Python 3.11 + Chromium 执行浏览器烟测（流水线尚未在实际 Runner 上运行）。

### 尚未完成

- 公司 OIDC/LDAP 尚未在应用内实现，目前通过 API Key 和反向代理预留接入边界。
- Docker/Podman 在开发机上未安装，镜像构建、Compose 验证和命名卷权限尚未验证。
- 尚未使用真实 GitLab CE 地址、项目 ID 和 Token 做联调。
- SQLite 尚未做多实例/高并发设计；需要扩展时迁移 PostgreSQL。
- 当前临时策略为记录和备份永久保留；没有应用内删除/清理接口或自动清理任务。若未来变更策略，应同时处理主库、本机安全副本和异机备份。

## 3. 架构和数据流

```text
Web / teamai-cli / Claude Code / Qwen Code
                    |
             TeamAI Capture API
                    |
       SQLite 元数据 + 本地记录内容
                    |
          可选 GitLab CE Issue 同步
```

设计原则：客户端不直接调用 GitLab。所有入口都调用 TeamAI API，由 API 负责分类、标题生成、脱敏、去重、外部 ID 关联和失败重试。

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
| `requirements-dev.txt` | 本地测试依赖；生产镜像只安装 `requirements.txt` |
| `skills/teamai-capture/SKILL.md` | Claude Code/Qwen Code 共享 Skill 说明 |
| `integrations/mcp-config.example.json` | MCP 配置示例 |
| `tests/test_api.py` | API 回归测试 |
| `tests/test_clients.py` | CLI/MCP 共用传输层安全与 HTTP 测试 |
| `tests/web/` | 详情焦点管理和 GitLab 手动重试交互的 Node 原生测试 |
| `scripts/browser_smoke.py` | 使用临时 SQLite 数据库执行 Playwright Chromium 桌面/手机烟测 |
| `PRODUCTION.md` | 部署前检查清单 |
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
python teamai_cli.py resolve 1 --solution "补充配置并重启"
```

默认 API 地址是 `http://127.0.0.1:8787`。可通过 `TEAMAI_API` 修改；配置鉴权时同时设置 `TEAMAI_API_KEY`。

### MCP Server

```powershell
python mcp_server.py
```

工具名包括：`teamai_create_note`、`teamai_create_issue`、`teamai_create_solution`、`teamai_search`、`teamai_get_issue`、`teamai_append_solution`、`teamai_resolve`。客户端配置参考 `integrations/mcp-config.example.json`。

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
| `POST` | `/v1/issues/{id}/resolve` | 标记已解决 |
| `POST` | `/v1/issues/{id}/reopen` | 重新打开 |
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
APP_HOST=127.0.0.1
APP_PORT=8787
TEAMAI_API_KEY=
GITLAB_URL=
GITLAB_TOKEN=
GITLAB_PROJECT_ID=
```

只有同时设置 `GITLAB_URL`、`GITLAB_TOKEN`、`GITLAB_PROJECT_ID` 时才启用 GitLab 同步。未配置时系统运行在本地模式，功能仍可完整使用。

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
- [ ] 在部署机或 CI 验证 Docker build、Compose 启动和数据卷持久化。
- 接入公司 OIDC/LDAP 反向代理，并确认用户身份映射、访问审计和权限边界。

### P2：规模和体验

- 使用真实 GitLab CE 做端到端联调。
- [x] 提供 SQLite 在线备份、完整性校验、带自动安全副本的恢复命令；备份先写同目录临时文件并校验，再原子发布且不覆盖已有目标，校验失败时清理临时文件。校验完整性、记录表及应用基础字段；同步扩展列允许由启动迁移补齐。尚未做部署环境定时任务、异机存储和恢复演练。
- 配置日志采集和告警。
- 当前按用户选择暂时永久保留记录和备份，不启用自动擦除或备份清理；未来若调整策略，再设计覆盖主库、安全副本和异机备份的可审计清理流程。
- 记录量增长后将全文搜索迁移到 PostgreSQL；需要语义检索时再评估 pgvector。
- 多实例部署前迁移 PostgreSQL，并增加任务队列或可靠的同步 worker。
- 已解决 Issue 自动沉淀为知识条目，补充负责人、技术栈和项目统计。

## 9. 验证方式

当前已验证：

- Python 3.11.15（uv 隔离环境，含完整开发依赖）：43 passed。
- Python 3.13.5（uv 隔离环境，含完整开发依赖）：43 passed，无警告。
- Node.js 22：18 个交互用例通过（详情焦点管理 4 个、外链协议校验 2 个、加载更多 3 个、请求错误提示 3 个、无障碍/色彩对比检查 4 个、GitLab 重试 2 个）。

```powershell
python -m pytest -q
# 43 passed

python -m py_compile app\main.py teamai_cli.py mcp_server.py teamai_backup.py teamai_transport.py
node --check web\app.js
node --check web\detail-focus.js
node --check web\safe-external-url.js
node --check web\load-more.js
node --check web\request-errors.js
node --check web\sync-retry.js
node --test tests\web\*.test.js
```

Playwright Chromium 浏览器烟测使用临时隔离数据库，可重复执行：

```powershell
pip install -r requirements-e2e.txt
python -m playwright install chromium
python scripts\browser_smoke.py
# 可选：python scripts\browser_smoke.py --screenshots-dir "$env:TEMP\teamai-browser-smoke"
```

已实测创建问题、打开详情并将焦点移到标题、解决/重开、Escape 关闭后返回同一记录，以及延迟列表刷新期间关闭详情后仍保持焦点；1440px 桌面和 390px 手机视口均无横向溢出，控制台无未捕获脚本错误。当前 Chromium 烟测连续通过 3 次。截图可选择保存到指定目录。Docker 构建和真实 GitLab 联调仍未验证。
已添加 `.gitlab-ci.yml`，在 GitLab Runner 上运行 Python 检查、全部前端脚本语法检查和 Node 交互测试，并安装 Playwright Chromium 执行隔离浏览器烟测；当前尚无 Runner 执行结果。

手工验证清单：

1. 打开首页，确认总览数字、记录列表和搜索框正常。
2. 使用类型、来源、状态过滤，打开一条记录详情。
3. 追加方案并执行解决，再执行重新打开。
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
