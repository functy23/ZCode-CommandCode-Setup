#!/bin/bash
# =============================================================
#  ZCode-CommandCode-Setup — 一键引导脚本（curl | bash）
#
#  用法：
#    curl -fsSL https://raw.githubusercontent.com/functy23/ZCode-CommandCode-Setup/main/install.sh | bash
#    curl -fsSL ... | bash -s -- --dry-run      # "--" 之后的参数原样传给配置脚本
#
#  做的事：把仓库里的 setup_commandcode.py 拉到临时目录并运行，
#  退出后自动删除，不在本地留下任何文件。
# =============================================================
set -uo pipefail

REPO="functy23/ZCode-CommandCode-Setup"
RAW_BASE="${ZCCS_BASE:-https://raw.githubusercontent.com/${REPO}/main}"

say() { printf '%s\n' "$*"; }
die() { printf '❌ %s\n' "$*" >&2; exit 1; }

[ "$(uname -s)" = "Darwin" ] || die "本脚本仅支持 macOS。"
command -v curl    >/dev/null 2>&1 || die "未找到 curl。"
command -v python3 >/dev/null 2>&1 || die "未找到 python3，请先执行：xcode-select --install"

TMP="$(mktemp -d "${TMPDIR:-/tmp}/ZCode-CommandCode-Setup.XXXXXX")" || die "无法创建临时目录。"
trap 'rm -rf "$TMP"' EXIT

TARGET="$TMP/setup_commandcode.py"
say "⬇️  下载 setup_commandcode.py ..."
curl -fsSL "$RAW_BASE/setup_commandcode.py" -o "$TARGET" \
  || die "下载失败。若仓库为私有会得到 404 —— 请确认仓库已公开，或 git clone 后本地运行。"
[ "$(head -c 2 "$TARGET")" = "#!" ] || die "下载内容不是有效脚本（可能被代理/防火墙拦截），已中止。"

say "🚀 启动配置脚本（退出后临时文件自动清理）..."
say "--------------------------------------------------------------"

# curl|bash 时 bash 的 stdin 是管道，交互输入会拿到 EOF —— 这里把 stdin
# 重新接到终端（/dev/tty），保证 Key 输入和确认提示可用；
# 无终端但已带 --key/--yes 时直接运行，否则明确报错。
has_flag() { local f="$1" a; shift; for a in "$@"; do [ "$a" = "$f" ] && return 0; done; return 1; }

if [ -t 0 ]; then
  python3 "$TARGET" "$@"
elif has_flag --key "$@" && { has_flag --yes "$@" || has_flag -y "$@"; }; then
  say "⚠️  无交互终端：已带 --key 与 --yes，跳过交互直接运行。"
  python3 "$TARGET" "$@"
elif ( : < /dev/tty ) 2>/dev/null; then
  python3 "$TARGET" "$@" < /dev/tty
else
  die "当前没有可交互终端：无法输入 Key。请加 --key <KEY>（以及 --yes）运行。"
fi
exit $?
