#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
CommandCode → ZCode 一键配置脚本（仅限 macOS）

功能：
  1. 校验运行环境（macOS + ZCode 配置文件存在）
  2. 提示输入 CommandCode API Key，并实时验证
  3. 拉取官方模型目录（GET /provider/v1/models）
  4. 对每个模型发送 1-token 最小请求，探测套餐可用性
     （200 = 可用；403 MODEL_NOT_IN_PLAN = 套餐不含；429/503 = 上游临时问题）
  5. 生成模型元数据（上下文/输出/模态/推理档位），写入 ~/.zcode/v2/config.json
     —— 写入前自动备份，可 --dry-run 预览

仅依赖 Python 3 标准库。模型探测会消耗极少额度（每模型 1~16 token）。
"""

import argparse
import getpass
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
import uuid
from concurrent.futures import ThreadPoolExecutor, as_completed

CONFIG_PATH = os.path.expanduser("~/.zcode/v2/config.json")
API_BASE = "https://api.commandcode.ai/provider/v1"
MODELS_PAGE = "https://commandcode.ai/models"
UA = ("Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/126.0 Safari/537.36")
DEFAULT_PROVIDER_NAME = "CommandCode"

# ---------------------------------------------------------------- 基础工具

def die(msg, code=1):
    print(f"\n❌ {msg}")
    sys.exit(code)


def info(msg):
    print(f"{msg}")


def ok(msg):
    print(f"✅ {msg}")


def warn(msg):
    print(f"⚠️  {msg}")


def http(url, key=None, body=None, method=None, timeout=60):
    """带浏览器 UA 的 HTTP 请求（该站会用 Cloudflare 1010 拦截非浏览器 UA）。
    返回 (status, 解析后的dict或原始str)。"""
    headers = {"Content-Type": "application/json", "User-Agent": UA}
    if key:
        headers["Authorization"] = f"Bearer {key}"
        headers["x-api-key"] = key
        headers["anthropic-version"] = "2023-06-01"
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers,
                                 method=method or ("POST" if data else "GET"))
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            raw = r.read().decode()
            return r.status, _try_json(raw)
    except urllib.error.HTTPError as e:
        raw = e.read().decode()
        return e.code, _try_json(raw)


def _try_json(raw):
    try:
        return json.loads(raw)
    except Exception:
        return raw


# ---------------------------------------------------------------- 环境检查

def check_macos():
    if platform.system() != "Darwin":
        die("本脚本仅支持 macOS。")


def check_config():
    if not os.path.isfile(CONFIG_PATH):
        die(f"未找到 ZCode 配置文件：{CONFIG_PATH}\n"
            "请先安装并启动一次 ZCode（生成配置后再运行本脚本）。")
    try:
        with open(CONFIG_PATH) as f:
            cfg = json.load(f)
    except Exception as e:
        die(f"配置文件不是合法 JSON，中止（请手工检查，勿让脚本覆盖）：{e}")
    if not isinstance(cfg.get("provider"), dict):
        die("配置文件缺少 provider 段，结构异常，中止。")
    return cfg


def zcode_running():
    """检测 ZCode（桌面版或 zcode-cli）是否在运行。"""
    try:
        out = subprocess.run(["pgrep", "-f", r"ZCode\.app|zcode-cli"],
                             capture_output=True, text=True, timeout=10)
        return out.returncode == 0
    except Exception:
        return False


# ---------------------------------------------------------------- 元数据表
# 以下规则表按"首条命中生效"，可按需增删（模型 id 上的正则）。
# 官网未提供输出上限/推理档位的机器可读数据，这些是经实测校准的默认值。

DEFAULT_OUTPUT = 65536
OUTPUT_RULES = [
    (r"deepseek/deepseek-v4", 384000),
    (r"xai/grok", 500000),
    (r"Kimi-K2\.7-Code", 262144),
    (r"Kimi-K3", 131072),
    (r"Kimi-K2\.[56]", 65536),
    (r"GLM-5\.3", 128000),
    (r"GLM-5\.2", 131072),
    (r"zai-org/GLM-5(\.1)?$", 64000),
    (r"gpt-5\.6", 128000),
    (r"tencent/hy", 64000),
    (r"mimo-v2\.5", 128000),
    (r"MiniMax-M3|minimax-m3", 131072),
    (r"m2\.7|M2\.7", 131072),
    (r"MiniMax-M2\.5", 65536),
    (r"Qwen/Qwen3\.8", 131072),
]

# (variants..., defaultVariant) 打包成 tuple，最后一个元素是默认档位
_R_HM = ("off", "high", "max", "max")
_R_HM_OFF = ("off", "high", "max", "off")
_R_LHM = ("low", "high", "max", "max")
_R_ON = ("enabled", "off", "enabled")
_R_OH = ("off", "high", "high")
REASONING_RULES = [
    (r"deepseek/deepseek-v4", _R_HM),
    (r"glm-5\.3-flash", _R_HM_OFF),
    (r"GLM-5\.3", _R_LHM),
    (r"GLM-5\.2", _R_HM),
    (r"zai-org/GLM-5(\.1)?$", _R_ON),
    (r"Kimi-K3", _R_HM),
    (r"Kimi-K2\.[56]", _R_ON),
    (r"Qwen/", _R_ON),
    (r"MiniMax-M3|minimax-m3", _R_ON),
    (r"mimo-v2\.5", _R_ON),
    (r"tencent/hy", _R_OH),
    (r"xai/grok", _R_OH),
    (r"gpt-5\.6", _R_HM),
]

INPUT_RULES = [
    (r"flash-vision-exp", ("text", "image")),
    (r"moonshotai/", ("text", "image", "video")),
    (r"Qwen/", ("text", "image", "video")),
    (r"MiniMax-M3|minimax-m3", ("text", "image", "video")),
    (r"mimo-v2\.5$", ("text", "image", "audio", "video")),
    (r"xai/grok", ("text", "image")),
    (r"gpt-5\.6|google/gemini", ("text", "image", "pdf")),
]


def _first_match(rules, model_id):
    for pattern, value in rules:
        if re.search(pattern, model_id):
            return value
    return None


def display_name(model_id):
    leaf = model_id.split("/")[-1]
    pretty = leaf.replace("-", " ")
    pretty = re.sub(r"\bV(\d)", r"V\1", pretty)
    for pat, rep in [
        (r"\bglm\b", "GLM"), (r"\bqwen\b", "Qwen"), (r"\bkimi\b", "Kimi"),
        (r"\bmimo\b", "MiMo"), (r"\bstep\b", "Step"), (r"\bgrok\b", "Grok"),
        (r"\bdeepseek\b", "DeepSeek"), (r"\bminimax\b", "MiniMax"),
        (r"\bgpt\b", "GPT"), (r"\bhy(\d)\b", r"Hy\1"),
        (r"\bcode\b", "Code"), (r"\bhighspeed\b", "HighSpeed"),
        (r"\bvision\b", "Vision"), (r"\bexp\b", "exp"),
        (r"\bfree\b", "Free"), (r"\bfast\b", "Fast"), (r"\bpro\b", "Pro"),
        (r"\bmax\b", "Max"), (r"\bplus\b", "Plus"), (r"\bflash\b", "Flash"),
        (r"\bpreview\b", "Preview"), (r"\bultra\b", "Ultra"),
    ]:
        pretty = re.sub(pat, rep, pretty, flags=re.I)
    pretty = re.sub(r"(\d)\s*\.\s*(\d)", r"\1.\2", pretty)
    return re.sub(r"\s+", " ", pretty).strip()


# ---------------------------------------------------------------- 上下文长度

def fetch_context_map():
    """从官网 models 页（SSR HTML）解析 显示名 → 上下文长度。失败返回 {}。"""
    try:
        status, html = http(MODELS_PAGE, timeout=30)
        if status != 200 or not isinstance(html, str):
            return {}
        text = re.sub(r"<(script|style)[^>]*>.*?</\1>", " ", html, flags=re.S | re.I)
        text = re.sub(r"<[^>]+>", "|", text)
        text = re.sub(r"\|+", "|", text)
        result = {}
        tokens = [t.strip() for t in text.split("|") if t.strip()]
        for i, tok in enumerate(tokens):
            m = re.fullmatch(r"(\d+(?:\.\d+)?)\s*([MK])", tok)
            if not m or i == 0:
                continue
            name = None
            for back in range(1, 7):
                cand = tokens[i - back]
                if re.match(r"^(Free|Deals|All|\d)", cand):
                    break
                if re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9 .()/&+-]{2,40}", cand) \
                        and not re.fullmatch(r"[a-z$]+", cand):
                    name = cand
                    break
            if name:
                mult = {"M": 1_000_000, "K": 1000}[m.group(2)]
                result.setdefault(name, int(float(m.group(1)) * mult))
        return result
    except Exception:
        return {}


def _norm(s):
    return re.sub(r"[^a-z0-9]", "", s.lower())


def context_for(model_id, ctx_map):
    """显示名与目录 id 归一化匹配；找不到回退 1M（该目录绝大多数模型为 1M）。"""
    if not ctx_map:
        return 1_000_000
    cands = {_norm(model_id.split("/")[-1]), _norm(model_id.split("/")[-1] + " (latest)")}
    best, best_len = None, 0
    for name, ctx in ctx_map.items():
        n = _norm(name)
        for c in cands:
            if c == n or (len(c) >= 6 and (c in n or n in c)):
                if len(n) > best_len:
                    best, best_len = ctx, len(n)
    return best or 1_000_000


# ---------------------------------------------------------------- 探测

def probe_model(model_id, key):
    """返回 (类别, 说明)。类别：OK / NOT_IN_PLAN / UNSUPPORTED / TRANSIENT / ERROR"""
    body = {"model": model_id, "max_tokens": 1,
            "messages": [{"role": "user", "content": "hi"}]}
    detail = ""
    for attempt in range(3):
        status, resp = http(f"{API_BASE}/chat/completions", key=key, body=body)
        if status == 200:
            return "OK", ""
        if status == 403:
            s = json.dumps(resp, ensure_ascii=False)
            return ("NOT_IN_PLAN", "套餐不含（MODEL_NOT_IN_PLAN）"
                    if "MODEL_NOT_IN_PLAN" in s else f"403: {s[:140]}")
        if status == 429 or status == 503:
            detail = f"{status} 上游暂时不可用"
            time.sleep(3 * (attempt + 1))
            continue
        if status == 400:
            s = json.dumps(resp, ensure_ascii=False) if not isinstance(resp, str) else resp
            if ">= 16" in s and body["max_tokens"] < 16:
                body["max_tokens"] = 16      # 部分模型要求 max_tokens >= 16
                continue
            return "UNSUPPORTED", s[:140]
        detail = f"{status}: {(resp if isinstance(resp, str) else json.dumps(resp, ensure_ascii=False))[:140]}"
        time.sleep(2)
    return ("TRANSIENT", detail or "多次重试失败") if detail.startswith("429") or detail.startswith("503") \
        else ("ERROR", detail)


def probe_all(model_ids, key, workers=8):
    results = {}
    done = 0
    with ThreadPoolExecutor(workers) as ex:
        futs = {ex.submit(probe_model, mid, key): mid for mid in model_ids}
        for fut in as_completed(futs):
            mid = futs[fut]
            try:
                results[mid] = fut.result()
            except Exception as e:                       # 网络层异常
                results[mid] = ("ERROR", str(e)[:140])
            done += 1
            cls, note = results[mid]
            mark = {"OK": "✅", "NOT_IN_PLAN": "🚫", "UNSUPPORTED": "❌",
                    "TRANSIENT": "⏳", "ERROR": "❌"}.get(cls, "❌")
            print(f"  [{done:>2}/{len(model_ids)}] {mark} {mid:42s} {note}")
            time.sleep(0.2)                              # 温和限速
    return results


# ---------------------------------------------------------------- 配置写入

def find_provider(cfg, name):
    """优先按 baseURL 识别 CommandCode，其次按 name。返回 (pid, provider) 或 (None, None)。"""
    for pid, p in cfg["provider"].items():
        if isinstance(p, dict) and "commandcode.ai" in str(p.get("options", {}).get("baseURL", "")):
            return pid, p
    for pid, p in cfg["provider"].items():
        if isinstance(p, dict) and p.get("name") == name:
            return pid, p
    return None, None


def build_provider(name, key, models):
    return {
        "name": name,
        "kind": "openai-compatible",
        "options": {
            "apiKey": key,
            "apiKeyRequired": True,
            "baseURL": API_BASE,
        },
        "source": "custom",
        "models": models,
    }


def write_config(cfg, dry_run):
    if dry_run:
        warn("dry-run 模式：不写盘。")
        return None
    stamp = time.strftime("%Y%m%d-%H%M%S")
    backup = f"{CONFIG_PATH}.bak-{stamp}"
    shutil.copy2(CONFIG_PATH, backup)
    with open(CONFIG_PATH, "w") as f:
        json.dump(cfg, f, ensure_ascii=False, indent=2)
    with open(CONFIG_PATH) as f:                          # 回读校验
        json.load(f)
    return backup


# ---------------------------------------------------------------- 主流程

def main():
    ap = argparse.ArgumentParser(
        description="将 CommandCode（GOAT 套餐）一键配置进 ZCode（仅 macOS）")
    ap.add_argument("--key", help="API Key（不传则交互式输入，也可用环境变量 COMMANDCODE_API_KEY）")
    ap.add_argument("--provider-name", default=DEFAULT_PROVIDER_NAME,
                    help=f"provider 显示名（默认 {DEFAULT_PROVIDER_NAME}）")
    ap.add_argument("--dry-run", action="store_true", help="只探测和预览，不写配置")
    ap.add_argument("--yes", "-y", action="store_true", help="跳过所有交互确认")
    ap.add_argument("--skip-probe", action="store_true",
                    help="跳过套餐探测，直接收录目录里除 Claude 外的全部模型")
    args = ap.parse_args()

    print("=" * 64)
    print(" CommandCode → ZCode 配置助手（macOS）")
    print(" 注意：当前仅支持不含 Claude 的套餐，Claude 模型会自动跳过")
    print("       模型目录变化较快，收录结果以本次实时探测为准")
    print("=" * 64)

    check_macos()
    cfg = check_config()

    key = args.key or os.environ.get("COMMANDCODE_API_KEY")
    if not key:
        key = getpass.getpass("请输入 CommandCode API Key（user_ 开头，输入不回显）：").strip()
    if not key:
        die("未输入 Key，退出。")

    # 1. 验证 Key + 拉目录
    print("\n[1/4] 验证 Key 并拉取模型目录 ...")
    status, resp = http(f"{API_BASE}/models", key=key)
    if status in (401, 403) and not (isinstance(resp, dict) and "data" in resp):
        die(f"Key 验证失败（HTTP {status}）：{str(resp)[:200]}")
    if not (isinstance(resp, dict) and isinstance(resp.get("data"), list)):
        die(f"模型目录返回异常：{str(resp)[:200]}")
    catalog = [m["id"] for m in resp["data"] if isinstance(m, dict) and m.get("id")]
    ok(f"Key 有效，目录共 {len(catalog)} 个模型")

    # 2. ZCode 运行检测
    if zcode_running():
        warn("检测到 ZCode 正在运行。")
        warn("脚本仍会写入（改完需完全退出并重启 ZCode 才生效）；")
        warn("注意：之后不要在 ZCode 设置界面里改 provider，否则界面可能用旧配置覆盖本文件。")
        if not args.yes and not args.dry_run:
            if input("继续写入吗？[y/N] ").strip().lower() not in ("y", "yes"):
                die("已取消，未做任何修改。", 0)
    elif not args.dry_run:
        ok("ZCode 未在运行，适合写入。")

    # 3. 探测
    print("\n[2/4] 探测各模型在当前套餐下的可用性（每模型 1~16 token，耗时约 1~3 分钟）...")
    if args.skip_probe:
        warn("已跳过探测（--skip-probe）。")
        results = {mid: ("OK", "跳过探测") for mid in catalog}
    else:
        results = probe_all(catalog, key)

    included, excluded = [], []
    for mid in catalog:
        cls, note = results.get(mid, ("ERROR", "无结果"))
        if mid.startswith("claude"):
            excluded.append((mid, "Claude 系只能走 Anthropic Messages 端点，"
                                  "与本 provider（openai-compatible）不兼容"))
        elif cls == "OK" or cls == "TRANSIENT":
            included.append(mid)                          # 429/503 属临时问题，保留
        else:
            excluded.append((mid, note))

    if not included:
        die("没有任何模型可用（可能 Key 或套餐异常），未写入任何配置。")

    # 4. 组装并写入
    print(f"\n[3/4] 组装模型元数据（上下文来自官网 models 页，输出/推理档位用内置规则）...")
    ctx_map = fetch_context_map()
    info(f"      官网上下文数据解析到 {len(ctx_map)} 个模型" if ctx_map
         else "      官网数据抓取失败，全部回退 1M 上下文")

    models = {}
    for mid in included:
        reason = _first_match(REASONING_RULES, mid)
        entry = {
            "name": display_name(mid),
            "limit": {"context": context_for(mid, ctx_map),
                      "output": _first_match(OUTPUT_RULES, mid) or DEFAULT_OUTPUT},
            "modalities": {"input": list(_first_match(INPUT_RULES, mid) or ("text",)),
                           "output": ["text"]},
            "zcode": {"modalitiesConfigured": True},
        }
        if reason:
            entry["reasoning"] = {"enabled": True,
                                  "variants": list(reason[:-1]),
                                  "defaultVariant": reason[-1]}
        models[mid] = entry

    pid, existing = find_provider(cfg, args.provider_name)
    provider = build_provider(args.provider_name, key, models)
    if existing:
        keep = existing.get("options", {}).get("apiKey")
        if keep and key == keep:
            pass
        cfg["provider"][pid] = provider
        action = f"更新已有 provider（id {pid[:8]}…）"
    else:
        pid = str(uuid.uuid4()).lower()
        cfg["provider"][pid] = provider
        action = f"新建 provider（id {pid[:8]}…）"

    print(f"\n[4/4] {'预览' if args.dry_run else '写入'}：{action}，"
          f"kind=openai-compatible，baseURL={API_BASE}")
    info(f"      收录模型 {len(included)} 个")

    if excluded:
        info("      未收录：")
        for mid, why in sorted(excluded):
            info(f"        · {mid}  —  {why}")

    backup = write_config(cfg, args.dry_run)
    if backup:
        ok(f"已写入 {CONFIG_PATH}（备份：{backup}）")
    print("""
后续步骤：
  1. 完全退出并重启 ZCode（⌘Q，不是关窗口）
  2. 模型选择器里即可看到 CommandCode 的模型
  3. 套餐/目录变化后可重跑本脚本刷新模型列表
""")


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n已中断，未保存任何修改。")
        sys.exit(130)
