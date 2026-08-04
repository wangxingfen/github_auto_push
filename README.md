# GitHub Auto

一个 AI 工具：自动分析指定文件夹里的项目，生成精美、可直接用于 GitHub 的 `README.md`，并自动创建 GitHub 仓库、推送代码。

## 功能

- **自动分析项目**：识别编程语言、代码规模、包管理元数据（package.json / pyproject.toml / requirements.txt / Cargo.toml / go.mod 等）、许可证、CI 工作流、git 提交记录
- **AI 生成精美 README**：调用 OpenAI 兼容接口（`/chat/completions`），输出带徽章、快速开始、项目结构等完整结构的 README；未配置 API Key 时自动回退到内置模板
- **可视化 Web 界面**：`web` 命令启动本地网页版，文件夹浏览器、项目分析面板、README 编辑/实时预览、日志流、一键推送，全中文界面
- **直接推送 GitHub**：项目没有远程仓库时自动通过 GitHub REST API 建仓并添加 `origin`，然后提交并推送；已有远程时直接推送
- **安全**：GitHub token 通过环境变量或 `config.json` 提供；推送时通过 git 环境变量注入认证头，token 不会出现在命令行参数中
- **批量处理**：`run` 命令扫描根目录下的所有子项目，逐个生成并推送，最后输出汇总
- **纯标准库**：只依赖 Python 3.10+ 和 git，无第三方包

## 快速开始

### 1. 配置密钥

推荐使用环境变量（PowerShell）：

```powershell
$env:OPENAI_API_KEY = "sk-..."
$env:GITHUB_TOKEN = "ghp_..."
```

也可以复制 `config.example.json` 为 `config.json` 后填写：

```json
{
  "llm": { "api_key": "sk-...", "base_url": "https://api.openai.com/v1", "model": "gpt-4o-mini" },
  "github": { "token": "ghp_...", "author_name": "你的名字", "author_email": "you@example.com" },
  "readme_lang": "auto",
  "private": false
}
```

说明：

- `config.json` 已被加入 `.gitignore`，不会误提交密钥
- `LLM_BASE_URL` / `LLM_MODEL` 可切换任意 OpenAI 兼容服务（DeepSeek、通义、Moonshot 等），例如 `LLM_BASE_URL=https://api.deepseek.com/v1 LLM_MODEL=deepseek-chat`
- GitHub token 需要 `repo`（或 `public_repo`）权限
- `readme_lang` 支持 `auto`（根据已有 README 语言判断）、`zh`、`en`
- `verify_ssl`（默认 `true`）：代理/公司网络下如遇 SSL 证书校验失败，可设为 `false` 跳过校验，或用 `ca_bundle` 指向公司 CA 证书文件；环境变量 `SSL_VERIFY=false` 同样生效
- `proxy`（默认空）：留空=跟随系统代理；`"none"`=强制直连；也可指定代理地址如 `"http://127.0.0.1:7890"`；环境变量 `PROXY` 同样生效

### 2. 分析单个项目并生成 README

```powershell
python github_auto.py analyze D:\projects\my-app
```

常用参数：

- `--lang zh|en`：指定 README 语言
- `--force`：覆盖已有 README
- `--no-ai`：跳过 AI，只用内置模板
- `--dry-run`：只预览，不写入文件、不调用 API
- `--output <路径>`：把 README 写到指定位置

### 3. 可视化界面（推荐）

```powershell
python github_auto.py web
```

命令会自动打开浏览器访问 `http://127.0.0.1:8765`，界面支持：

- 文件夹浏览器：点击「浏览…」逐级选择目录，或直接粘贴路径后「扫描项目」
- 项目列表：自动发现根目录下所有项目（`.git` / 清单文件 / 源码文件）
- 分析面板：语言分布、代码规模、许可证、CI 工作流、元数据、目录树、git 信息
- README 工作区：一键生成（AI 或模板）、左右分栏实时预览、直接编辑保存
- 推送面板：选择公开/私有仓库、自定义提交信息、一键推送，日志实时滚动
- 右上角徽章实时显示 LLM / GitHub token 是否已配置；设置里可切换模型、API 地址、README 语言（本次会话生效）

其他参数：`--port 9000` 换端口，`--host 0.0.0.0` 允许局域网访问，`--no-browser` 不自动打开浏览器。

### 4. 批量处理一个文件夹

```powershell
python github_auto.py run D:\projects --private
```

扫描 `D:\projects` 下的每个子项目（需要包含 `.git`、清单文件或至少 2 个源码文件），为每个项目生成 README 并推送 GitHub。加上 `--dry-run` 可先预览全部操作。

### 5. 只推送已有项目

```powershell
python github_auto.py push D:\projects\my-app
```

如果项目已有远程仓库就直接提交推送；没有远程仓库且配置了 `GITHUB_TOKEN` 时自动建仓（仓库名取自文件夹名，重名会自动追加 `-2`、`-3`）。

### 6. 一键诊断网络/代理

```powershell
python github_auto.py doctor
```

输出当前配置（token、模型、代理）、git 代理设置，并实际测试 GitHub API、`git → github.com` 和 LLM 接口的连通性，连接问题一目了然。

## 工作原理

```text
指定文件夹
    │
    ├─ 发现子项目（.git / 清单文件 / 源码文件）
    │
    ├─ 分析：语言、行数、元数据、许可证、git 信息、目录树
    │
    ├─ 生成：AI 撰写 README（无 Key 时回退内置模板）
    │
    └─ 推送：git init → 提交 → 无远程则建仓 → push
```

可视化界面复用同一套分析/生成/推送引擎，只是多了网页操作层。

## 常见问题

**没有 OpenAI API Key 能用吗？**
能。工具会自动使用内置模板生成一份结构完整的 README（无徽章之外的 AI 效果）。

**token 会泄露吗？**
推送时通过 `GIT_CONFIG_COUNT` / `GIT_CONFIG_KEY_0` / `GIT_CONFIG_VALUE_0` 环境变量注入 `Authorization` 头，token 不会出现在命令行或进程列表中；远程地址中也不含 token。

**创建的仓库是公开还是私有？**
默认公开，加 `--private` 或把配置里的 `private` 设为 `true` 创建私有仓库。

**项目本身不是 git 仓库？**
工具会自动 `git init -b main` 并设置本地 git 身份。

**报错 `CERTIFICATE_VERIFY_FAILED` / 无法连接 GitHub API？**
通常是代理、VPN 或安全软件在做 SSL 中间人拦截，其证书不在 Python 自带的证书库中。工具默认会自动加载 Windows 系统证书库（浏览器能访问就能通）。若仍失败，按情况选择：

- 临时跳过校验（仅推荐内网/代理环境）：在 `config.json` 中设置 `"verify_ssl": false`，或设置环境变量 `SSL_VERIFY=false`，Web 界面也可在「设置」里勾选
- 正规做法：把代理/公司根证书导出为 PEM（`certmgr.msc` → 证书 → 导出 Base-64 编码），在 `config.json` 里用 `"ca_bundle": "C:\\path\\ca.pem"` 指定

GitHub API、AI 接口和 git push 三处都会遵循该设置。

**报错 `WinError 10061` 连接被拒绝 / 无法连接 GitHub API？**
通常是系统代理（Clash、v2rayN 等）配置了但软件没启动，或代理端口不对。工具会自动检测并提示当前代理，且代理连接被拒绝时会自动尝试直连。仍失败时按情况处理：

- 确认代理软件已启动；或把 `config.json` 里的 `"proxy"` 改成实际代理地址（如 `"http://127.0.0.1:7890"`）
- 不想走代理：设置 `"proxy": "none"` 强制直连
- 若系统代理是 SOCKS（`socks5://…`），Python 标准库不支持，请换成 HTTP 代理地址

Web 界面「设置」里也可以直接配置代理（留空=系统代理、`none`=直连、或填写代理地址），保存后立即生效。

**推送时报 `Failed to connect to github.com port 443 via 127.0.0.1`？**
说明 **git 自身配置了代理**（`git config http.proxy` 或 `https.proxy`）但代理没在运行。工具会在推送时检测并打印 git 代理配置；连接失败会自动尝试禁用代理直连一次。仍失败时：

- 启动代理软件后重试（GitHub API 能通不代表 git 能通，两者使用不同的代理通道）
- 或移除 git 的代理配置：`git config --global --unset http.proxy` 和 `git config --global --unset https.proxy`
- 或在 `config.json` 设置 `"proxy": "none"` 强制直连（仅当你的网络可以直连 GitHub 时）

先用 `python github_auto.py doctor` 看 git 代理配置和连通性，再决定用哪种方式。

**推送时报 `remote: invalid credentials` / `Authentication failed`？**
两种常见原因，工具都已自动处理：

- GitHub 的 git 服务不接受经典 PAT 的 `Bearer` 头，必须用 `Basic` 认证——工具已改为 `Authorization: Basic`（`x-access-token:<token>`）并实测可用
- 某些 IDE（如 Trae CN）会设置坏掉的 `GIT_ASKPASS` 脚本，git 需要凭据时会卡死——推送时工具会禁用交互提示并移除该变量

推送会按顺序自动尝试：先用配置的 token（Basic 认证）→ 失败则用 git 已有凭据（如 Git Credential Manager）→ 连接类失败再禁用代理直连。全部失败时错误信息会说明原因。若 token 本身失效，请更新 `config.json` 中的 token 或运行 `git credential-manager github login`。

**报错 `git config user.name ... 失败: fatal: not in a git directory`？**
项目文件夹里有一个**空的或损坏的 `.git`**（某些 IDE 如 Trae CN 在打开文件夹时会创建空 `.git` 占位），工具误以为已经是仓库而跳过了初始化。现在工具会先用 `git rev-parse` 验证仓库有效性：无效就自动重新 `git init`；损坏的 `.git` 会先备份为 `.git.bak-*` 再重建（不会删除数据）。直接重试推送即可。

**API 报 SSL 证书错误、但 git 推送正常（或反之）？**
代理对 `api.github.com` 和 `github.com` 的 TLS 中转行为可能不同。工具对 GitHub API / AI 接口会自动按「走代理 → 直连 → 关闭校验走代理」降级重试，直连通常更可靠；git 推送则按「token Basic 认证 → 已有凭据 → 直连」重试。两类通道独立处理，任一条失败都不影响另一条。

**GitHub 上已经有同名仓库？**
不会再去创建 `xxx-2` 这类新仓库了：同名仓库存在时直接使用现有仓库并推送。现有仓库是空的（比如之前推送失败留下的）会直接推入 `main`；如果远端已有不同的提交历史导致 `main` 被拒绝，会自动改推一个新分支（`auto-日期时间`），不会覆盖你的旧数据。代理连续失败时也会记住状态，后续请求直接走直连，不再每条都报警告。

## 许可证

MIT
