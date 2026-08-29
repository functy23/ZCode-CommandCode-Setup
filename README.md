# ZCode-CommandCode-Setup

一键把 **CommandCode** 配置进 **ZCode** 的 macOS 脚本：输入 API Key，自动探测套餐内可用模型并写入配置。只依赖 macOS 自带的 Python 3，无需安装任何第三方包。

> ⚠️ **当前版本仅支持不含 Claude 的套餐**（如 GOAT）。
>
> - **Claude 暂不支持**：CommandCode 的 Claude 系模型只能走 Anthropic Messages 端点，与本脚本创建的 `openai-compatible` provider 不兼容；且**作者本人的套餐不含 Claude 系 API**，无法获取并验证，故暂不支持。脚本会**自动跳过所有 Claude 模型**——套餐含 Claude 时其余模型照常收录；确需 Claude，请另行配置 `anthropic` 类型的 provider。
> - **模型列表有时效性**：CommandCode 的模型目录变化频繁，个别更新较快的模型可能调用失败或已被官方下架/取消。收录结果以运行脚本时的实时探测为准，遇到这种情况重跑本脚本刷新即可。

## 使用方法

**方式一（推荐）**：curl 一键运行——不下载仓库、不留本地文件，脚本拉到临时目录跑完自动清理：

```bash
curl -fsSL https://raw.githubusercontent.com/functy23/ZCode-CommandCode-Setup/main/install.sh | bash
```

参数通过 `-s --` 透传，例如先预览：

```bash
curl -fsSL https://raw.githubusercontent.com/functy23/ZCode-CommandCode-Setup/main/install.sh | bash -s -- --dry-run
```

> 注意：该方式要求本仓库为**公开**（私有仓库会 404）。

**方式二**：双击 `run.command`，按提示粘贴 API Key。

**方式三（终端）**：

```bash
git clone https://github.com/functy23/ZCode-CommandCode-Setup.git   # 或直接下载本项目
cd ZCode-CommandCode-Setup
python3 setup_commandcode.py                 # 交互式：提示输入 Key
python3 setup_commandcode.py --key user_xxx  # 直接传 Key
python3 setup_commandcode.py --dry-run       # 只探测和预览，不写配置
python3 setup_commandcode.py --skip-probe    # 跳过探测，直接收录目录内全部非 Claude 模型
```

环境变量 `COMMANDCODE_API_KEY` 也可以用来传 Key。

## 脚本做了什么

1. **环境检查**：仅限 macOS；确认 `~/.zcode/v2/config.json` 存在且为合法 JSON；检测 ZCode 是否在运行（在运行会提示风险并确认）。
2. **验证 Key**：请求 `GET https://api.commandcode.ai/provider/v1/models`，Key 无效立即退出，不碰配置。
3. **拉取模型目录**：获取官方全量模型清单（id 带厂商前缀，如 `zai-org/GLM-5.3`）。
4. **套餐探测**：对每个模型发一条 `max_tokens=1`（部分模型 16）的最小请求：
   - `200` → 套餐内可用，收录；
   - `403 MODEL_NOT_IN_PLAN` → 套餐不含，排除；
   - `429/503`（重试 3 次仍失败）→ 上游临时问题，仍收录；
   - `400` → 模型 id/端点不兼容，排除。
   Claude 系模型直接跳过（它们只能走 Anthropic Messages 端点，与本脚本创建的 `openai-compatible` provider 不兼容）。
5. **生成元数据并写入**：上下文长度取自 commandcode.ai/models 页面；输出上限、输入模态、推理档位按脚本内置规则表（可编辑）。写入前自动备份，写入后回读校验。

## 参数

| 参数 | 说明 |
|---|---|
| `--key` | API Key（否则交互输入；也可用环境变量 `COMMANDCODE_API_KEY`） |
| `--provider-name` | provider 显示名，默认 `CommandCode` |
| `--dry-run` | 只探测与预览，不写盘 |
| `--yes` / `-y` | 跳过交互确认（ZCode 在运行时也会直接写） |
| `--skip-probe` | 跳过套餐探测（省时间，但可能收录套餐外的模型） |

## 安全说明

- 每次写入前都会生成备份：`~/.zcode/v2/config.json.bak-<时间戳>`。
- 脚本只新增/覆盖 `provider` 下 CommandCode 一个条目（按 baseURL 含 `commandcode.ai` 识别，重复运行是更新而不是重复添加），其余内容原样保留。
- API Key 只写入 ZCode 配置文件，不打印、不落日志。
- 探测请求每模型消耗 1~16 token，整轮通常折合几美分以内。

## 写入后的操作

**完全退出并重启 ZCode**（⌘Q，不是关窗口），模型选择器里即可看到 CommandCode 的模型。重启前不要在 ZCode 设置界面里改 provider，避免界面用内存中的旧配置覆盖文件。

## 常见问题

- **`401/403 Key 验证失败`**：Key 抄错或已失效；Key 以 `user_` 开头，在 CommandCode 后台获取。
- **探测大量 `❌ 403 ... 1010`**：Cloudflare 拦截了请求。脚本已内置浏览器 User-Agent；若仍出现，说明网络环境有代理/防火墙注入，换网络重试。
- **`400 ... is not supported on this endpoint`**：模型家族与端点不匹配。Claude 系必须走 Anthropic 端点，本脚本不支持配置 Claude；其余模型走 chat/completions 即可。
- **调用时 404 `not a registered API route`**：配置里 baseURL 被改坏（多写了端点路径）。重跑本脚本即可修复为 `https://api.commandcode.ai/provider/v1`。
- **改完不生效**：没有完全重启 ZCode。
- **想回滚**：用最新的 `config.json.bak-*` 覆盖回去，再重启 ZCode。

## 已知边界（2026-08-29 校准）

- **仅支持不含 Claude 的套餐**（见顶部说明）：因为作者没有 Claude 系模型的 API，无法开发与验证，暂不给予支持；Claude 系模型一律自动跳过。GOAT 套餐本身也不含 Claude / 多数 GPT / 多数 Gemini，探测会一并排除。
- **模型目录变化频繁**：可能导致某些更新频率较快的模型无法使用（上游故障、限流）或被官方取消/下架。脚本按运行时的实时探测结果决定收录与否——`429/503` 视为临时问题仍会收录，`400/403` 会排除；发现某个模型突然不能用时，重跑本脚本刷新列表。
- `taste-1` 虽在套餐内但不在 API 目录中，无法配置。
- 内置的输出上限/推理档位规则表位于脚本 `OUTPUT_RULES` / `REASONING_RULES` / `INPUT_RULES`，新模型上线后可按需补充。
