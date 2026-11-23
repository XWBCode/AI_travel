#!/usr/bin/env bash
# 本地跑一遍 CI 的三道门禁（GitHub Actions 跑的是同一组命令）。
#
# 为什么要有这个脚本：CI 是「push 之后才知道」，本地这个脚本是「提交之前就能知道」。
# 两边命令必须一致，否则会出现「本地绿、CI 红」——那时候排查成本已经翻倍了。
#
# 用法：
#   bash scripts/ci-local.sh              # 三端全跑
#   bash scripts/ci-local.sh --fast       # 只跑 python + frontend（约 1 分钟）
#   ISOLATION=1 bash scripts/ci-local.sh  # 额外逐类隔离跑 Java 测试（约 10 分钟，见 BUG-017）
#   SKIP_JAVA=1 bash scripts/ci-local.sh  # 没有本地 MySQL 时跳过 Java 集成测试
#
# 退出码 0 = 全绿。任何一步失败立即中断并返回非 0。

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
FAST=0
[[ "${1:-}" == "--fast" ]] && FAST=1

# 本地数据库口令从 .env 读（CI 里由 service 容器提供），不写死在脚本里
if [[ -f "$ROOT/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  . "$ROOT/.env"
  set +a
fi

step() { printf '\n\033[1m==> %s\033[0m\n' "$1"; }

step "Python 单测（不连库、不调模型）"
cd "$ROOT/backend-ai"
if [[ -x .venv/Scripts/python.exe ]]; then PY=.venv/Scripts/python.exe; else PY=.venv/bin/python; fi
"$PY" -m pytest -q

step "前端类型检查"
cd "$ROOT/frontend"
npx vue-tsc --noEmit

step "前端测试"
npm test

if [[ "$FAST" == "1" ]]; then
  printf '\n\033[32m--fast 模式：已跳过 Java\033[0m\n'
  exit 0
fi

if [[ "${SKIP_JAVA:-0}" == "1" ]]; then
  printf '\n\033[33mSKIP_JAVA=1：已跳过 Java 集成测试（本地无 MySQL 时用，CI 里不会跳过）\033[0m\n'
  exit 0
fi

step "Java 编译 + 单测（集成测试需要 ai_travel_test 库）"
cd "$ROOT/backend-java"
mvn -B -q test

# 默认不跑：逐类隔离要 10 分钟。提交前改动了测试基础设施、或怀疑顺序依赖时开 ISOLATION=1。
# 「全量绿」不等于「每类都对」——BUG-017 就是全量绿而单跑必红的例子。
if [[ "${ISOLATION:-0}" == "1" ]]; then
  step "Java 测试逐类隔离运行（ISOLATION=1）"
  bash scripts/test-isolation.sh
fi

printf '\n\033[32m全绿：Python / 前端 / Java 三道门禁通过\033[0m\n'
