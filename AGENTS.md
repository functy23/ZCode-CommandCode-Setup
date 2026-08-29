# AGENTS.md — ZCode-CommandCode-Setup 项目说明（AI 通用）

> 本文件供任何 AI 助手（ZCode / Claude Code / Codex / 其他 agent）在本仓库工作时阅读。
> 目标：让接手的 agent **不依赖历史会话**就能安全地修改、扩展、排障。
> 标注【实测】的事实已在 2026-08-29 用作者自己的 API key 验证；标注【推断】的是合理推断，未直接验证。
> 面向人类的使用说明在 `README.md`，本文件面向维护者/agent，两者互补。

---

## 1. 项目是什么（TL;DR）

一键把 **CommandCode**（模型聚合服务，作者订阅的是 GOAT 套餐）配置成 **ZCode**（`~/.zcode/v2/config.json`）里的自定义 provider。用户只需提供 API Key，脚本自动：验证 Key → 拉模型目录 → 逐个探测套餐可用性 → 生成模型元数据 → 备份并写入配置。

- 语言/依赖：纯 Python 3 标准库 + bash；**仅支持 macOS**。
- 仓库：`github.com/functy23/ZCode-CommandCode-Setup`（public，分支 `main`）。
- 一键入口：`curl -fsSL https://raw.githubusercontent.com/functy23/ZCode-CommandCode-Setup/main/install.sh | bash`

## 2. 文件清单

| 文件 | 作用 | 关键点 |
|---|---|---|
| `setup_commandcode.py` | 主脚本（全部业务逻辑） | 只依赖标准库；唯一会写盘的程序 |
| `install.sh` | curl 一键引导脚本 | 拉主脚本到临时目录运行，退出自动清理；不落盘 |
| `run.command` | macOS 双击启动器 | 仅 `cd` 到自身目录并 `exec python3 setup_commandcode.py`，无逻辑 |
| `README.md` | 人类用户文档 | 用法/参数/FAQ/已知边界 |

## 3. 领域事实（改代码前必读）

### 3.1 ZCode 配置文件

- 路径：`~/.zcode/v2/config.json`，顶层只有 `provider` 一个键，其下以 provider UUID 为键。
- provider 条目：`name / kind / source:"custom" / options{apiKey, baseURL, apiKeyRequired} / models{...}`。
- model 条目：`name / limit{context,output} / modalities{input,output} / reasoning{enabled,variants,defaultVariant}(可选) / zcode.modalitiesConfigured`。
- **路由规则【实测】**：ZCode 会把端点路径追加到 `baseURL` 后——
  - `kind:"anthropic"` → `{baseURL}/v1/messages`
  - `kind:"openai-compatible"` → `{baseURL}/chat/completions`（本项目用的就是它）
  - `kind:"openai"` → Responses API 路径（按内置 provider 命名推断为 `/responses`）【推断】
  - 因此 `baseURL` **绝不能**包含端点路径本身（历史上 404 的根因就是多写了 `/v1/messages`）。
- 改完配置必须**完全退出并重启 ZCode**；ZCode 运行时不要在它的设置 UI 里改 provider（UI 会把内存旧配置整体回写，覆盖手工修改）。

### 3.2 CommandCode API【全部实测】

| 端点 | 方法 | 说明 | 鉴权 |
|---|---|---|---|
| `/provider/v1/models` | GET | 全量模型目录（2026-08-29 时 62 个，id 带厂商前缀） | Bearer |
| `/provider/v1/messages` | POST | **仅 Claude 系**，Anthropic Messages 形态 | `x-api-key` + `anthropic-version: 2023-06-01` |
| `/provider/v1/chat/completions` | POST | **OpenAI/OSS 全部模型**，Chat Completions 形态 | Bearer |

- 模型 id 必须与目录逐字符一致（含大小写与 `/`），如 `zai-org/GLM-5.3`、`deepseek/deepseek-v4-flash`、`moonshotai/Kimi-K3`。
- 错误签名：404 `not a registered API route`=路径重复；400 `not supported on this endpoint`=模型放错端点；403 `MODEL_NOT_IN_PLAN`=套餐不含；400 `No available providers match the 'only' filter`=上游路由故障；429/503=上游临时过载。
- **Cloudflare 1010**：该站拦截非浏览器 UA，python `urllib` 默认 UA 必被拦。脚本统一带 Chrome UA（`UA` 常量），排障时自己发请求也要带。
- 官网 `commandcode.ai/models` 与 `/pricing` 是 SSR 页面（HTML 内嵌模型上下文/价格/套餐数据）；`/docs/<子页>` 全是 SPA 404 壳，抓不到内容。
- Claude 模型只能走 `/messages` 端点 → 与本项目的 `openai-compatible` provider 不兼容 → 脚本**自动跳过所有 `claude*` 模型**。作者套餐不含 Claude，无法开发/验证，**暂不支持**；将来要支持，做法是另建一个 `kind:"anthropic"`、`baseURL:"https://api.commandcode.ai/provider"` 的 provider。
- GOAT 套餐（2026-08-29）：不含 Claude、多数 GPT/Gemini（仅 gemini-3.7-flash、gpt-5.6-luna/sol 可用）；`taste-1` 在套餐内但不在 API 目录，无法配置。
- 套餐判定法：对每个模型发 `max_tokens=1` 最小请求，200=可用，403 `MODEL_NOT_IN_PLAN`=不含。这是唯一可靠口径，官网页面只做参考。

## 4. `setup_commandcode.py` 结构

### 4.1 常量（文件头部）

```python
CONFIG_PATH = "~/.zcode/v2/config.json"     # 展开 ~
API_BASE    = "https://api.commandcode.ai/provider/v1"
MODELS_PAGE = "https://commandcode.ai/models"
UA          = Chrome 126 浏览器 UA           # 必须，否则 Cloudflare 1010
DEFAULT_PROVIDER_NAME = "CommandCode"
```

### 4.2 主流程（`main()`，步骤打印为 [1/4]~[4/4]）

1. `check_macos()` → `check_config()`（文件存在、JSON 合法、有 `provider` 段，否则中止）。
2. Key 来源优先级：`--key` > 环境变量 `COMMANDCODE_API_KEY` > `getpass` 交互输入。随后 `GET /models` 验证 Key，无效立即退出（不碰配置）。
3. `zcode_running()`（`pgrep -f "ZCode\.app|zcode-cli"`）：在运行则警告 + 交互确认（`--yes` 跳过）。
4. 探测：`probe_all()` 逐模型调 `probe_model()`，8 线程 + 每完成一个 `sleep(0.2)` 温和限速。探测消耗极小（每模型 1~16 token）。
5. 收录规则：
   - `claude*` → 一律排除（端点不兼容）；
   - `OK`（200）与 `TRANSIENT`（429/503 重试 3 次仍失败）→ 收录；
   - `NOT_IN_PLAN`（403）与 `UNSUPPORTED`（400）→ 排除。
   - `--skip-probe` 时全部当 `OK`（除 Claude）。
6. 元数据组装（见 4.3）。
7. `find_provider()`：先按 `options.baseURL` 含 `commandcode.ai` 找已有 provider，再按 name 匹配——**重复运行是更新，不会重复添加**；找不到则 `uuid4()` 新建。
8. `write_config()`：写前 `cp` 备份为 `config.json.bak-YYYYmmdd-HHMMSS`，写后回读校验 JSON。`--dry-run` 则完全不写盘。

### 4.3 元数据从哪来（准确性分层）

- **上下文**：`fetch_context_map()` 抓 `MODELS_PAGE` 的 SSR HTML，从 "1M/256K" 标记旁的显示名解析（2026-08-29 实测解析出 59 个）；`context_for()` 用归一化（去非字母数字）匹配显示名与目录 id，匹配不到回退 `1_000_000`（目录绝大多数是 1M）。
- **输出上限 / 推理档位 / 输入模态**：脚本内**手工校准的规则表**（按模型 id 正则，首条命中生效）：
  - `OUTPUT_RULES`（默认 65536）：如 deepseek-v4→384000、grok→500000、Kimi-K2.7-Code→262144、GLM-5.2→131072…
  - `REASONING_RULES`（命中不到就不写 `reasoning` 段，ZCode 会当普通模型）：档位 tuple 最后一个元素是 `defaultVariant`，如 `_R_HM=("off","high","max","max")`、`_R_ON=("enabled","off","enabled")`。
  - `INPUT_RULES`（默认 `("text",)`）：如 kimi/qwen→text,image,video、grok→text,image、gpt-5.6/gemini→text,image,pdf。
  - **新模型上线后优先补这三张表**，而不是改逻辑。值不对不会导致调用失败，但会导致上下文溢出提示不准/无法附图。
- `display_name()` 把目录 id 转显示名（分段、替换常见词大小写），仅影响 ZCode 里的展示。

### 4.4 CLI 参数

`--key`、`--provider-name`（默认 CommandCode）、`--dry-run`、`--yes/-y`、`--skip-probe`。全部可组合，详见 README。

## 5. `install.sh` 引导脚本（坑最多，改动需谨慎）

- 流程：平台/依赖检查 → `mktemp -d` 建临时目录 → `curl -fsSL` 拉 `setup_commandcode.py` → 校验前两字节是 `#!`（防代理返回错误页）→ 运行 → `trap EXIT` 自动 `rm -rf`。
- **macOS 自带 bash 3.2 兼容性【实测踩坑】**：`elif` 条件里对特殊内置命令 `:` 做重定向（如 `{ : < /dev/tty; }`）失败会**直接终止整个脚本**。探测终端可用性必须放进子 shell：`( : < /dev/tty ) 2>/dev/null`。
- stdin 分支顺序（curl|bash 时 stdin 是管道，交互会拿到 EOF）：
  1. `[ -t 0 ]` 有终端 → 直接运行；
  2. 参数同时含 `--key` 和 `--yes/-y`（`has_flag` 逐字比较）→ 无需交互，直接运行；
  3. 子 shell 探测 `/dev/tty` 可打开 → `python3 ... < /dev/tty`（让 getpass/input 可用）;
  4. 否则明确报错退出。
- `ZCCS_BASE` 环境变量可覆盖下载基址（测试用，见 §7）。
- ⚠️ **不要用 `exec python3 ...`**：exec 会替换 shell，`trap EXIT` 不触发，临时目录就泄漏了。

## 6. 约定（本仓库的硬规则）

1. **英文命名必须带大小写（PascalCase/UpperCamelCase）**——仓库名、文件名、分支等都如此（用户全局规则，见 `ZCode-CommandCode-Setup` 的写法）。
2. **绝不把 API key 提交进仓库**。key 只在运行时从参数/环境/交互输入获取，或存在于用户的 `~/.zcode/v2/config.json`。提交前 `grep -rInE "user_[A-Za-z0-9]{20,}|sk-"` 自查。
3. 文档与输出文案用中文；代码注释密度与现有风格保持一致（只在必要处注释）。
4. 提交信息：英文祈使句 + 简短说明（参考 `git log`）。
5. `set -uo pipefail` 的 bash 脚本要考虑 bash 3.2（macOS 默认），不要用 bash 4+ 特性（如 `&>`、关联数组）。

## 7. 测试与排障手册

```bash
# 主脚本预览（不写盘、不探测，最快）
python3 setup_commandcode.py --dry-run --yes --skip-probe --key "$KEY"

# 主脚本完整探测（会消耗少量额度，~1-3 分钟）
python3 setup_commandcode.py --dry-run --yes --key "$KEY"

# install.sh 本地端到端测试（不起 GitHub，用本地 http.server + ZCCS_BASE）
cd 仓库目录 && (python3 -m http.server 8761 &) 
ZCCS_BASE=http://127.0.0.1:8761 bash install.sh --dry-run --yes --skip-probe --key "$KEY"
# 场景矩阵：[终端直接跑] / [printf '' | bash ... 无终端+带key] / [无终端无key 应报错 exit 1]
#           / [script -q /dev/null bash ... 模拟 pty]
pkill -f "http.server 8761"

# 线上一键命令验证（raw CDN 对新推送有 ~1 分钟缓存延迟，改完别急着测）
curl -fsSL https://raw.githubusercontent.com/functy23/ZCode-CommandCode-Setup/main/install.sh | bash -s -- --dry-run

# 临时目录是否残留（应为 0）
ls -d "${TMPDIR:-/tmp}"/ZCode-CommandCode-Setup.* 2>/dev/null | wc -l
```

常见症状：
- 探测大量 403 + `error code: 1010` → UA 被拦（脚本已内置浏览器 UA；若仍出现是网络层注入）。
- 一键命令 404 → raw CDN 缓存未更新（等 1 分钟）或仓库转回私有。
- 配置改了不生效 → ZCode 没有完全重启；或被 ZCode 设置 UI 回写覆盖（对比 `config.json.bak-*`）。
- 收录数与预期不符 → `--skip-probe` 会收录目录里全部非 Claude 模型（62-7=55），正常探测则只收录 200/429/503 的（2026-08-29 为 43）。

## 8. 已知边界与待办

- 不支持 Claude / `anthropic` kind provider（作者无 Claude API，无法验证）；不支持 Linux/Windows。
- `laguna-s-2.1-free` 上下文在官网取不到，回退 1M（脚本）——README 曾记 256000，两处均为估计值。
- 模型目录变化频繁：收录以运行时探测为准；规则表（§4.3）需随新模型维护。
- 想加"探测结果缓存/并发限速调整/输出格式"等，改动集中在 `probe_model/probe_all`，勿动写入逻辑。

## 9. 变更历史

| 日期 | 变更 |
|---|---|
| 2026-08-29 | 立项：修复 ZCode 里 CommandCode provider 404 → 产品化为本脚本；改名 `ZCode-CommandCode-Setup`；新增 `install.sh`（含 bash-3.2 tty 坑修复）；本文件创建。 |
