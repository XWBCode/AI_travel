#!/usr/bin/env bash
# 逐个测试类单独跑一遍，证明没有「靠执行顺序才绿」的用例。
#
# 为什么需要这个脚本（BUG-017）：
#   `mvn test` 全绿并不代表每个测试类都对。IdempotencyServiceTest 曾经「单跑必红、全跑可能绿」——
#   它依赖 MyBatis-Plus 的静态元信息缓存，而那份缓存是别的测试类启动 Spring 上下文时顺手建好的。
#   这类问题比红更危险：本地红 / CI 绿、或者换个执行顺序就变脸，排查成本翻倍。
#
# 用法：
#   bash scripts/test-isolation.sh              # 全部测试类逐个跑（慢，约 10 分钟）
#   bash scripts/test-isolation.sh Idempotency  # 只跑类名匹配的（支持 grep 模式）
#
# 退出码 0 = 每个测试类单独跑都能过。

set -uo pipefail

cd "$(dirname "${BASH_SOURCE[0]}")/.."
PATTERN="${1:-.}"
LOG_DIR="target/isolation-logs"
mkdir -p "$LOG_DIR"

mapfile -t CLASSES < <(
  find src/test/java -name "*Test.java" \
    | sed 's|^src/test/java/||; s|/|.|g; s|\.java$||' \
    | grep -E "$PATTERN" \
    | sort
)

if [[ ${#CLASSES[@]} -eq 0 ]]; then
  echo "没有匹配的测试类：$PATTERN"
  exit 1
fi

echo "共 ${#CLASSES[@]} 个测试类，逐个单独运行（日志在 $LOG_DIR/）"
failed=()
skipped=()
ran=0
for cls in "${CLASSES[@]}"; do
  log="$LOG_DIR/${cls}.log"
  if mvn -o test -Dtest="$cls" -Dsurefire.failIfNoSpecifiedTests=false >"$log" 2>&1; then
    summary=$(grep -E "^\[INFO\] Tests run:" "$log" | tail -1 | sed 's/^\[INFO\] //')
    total=$(sed -n 's/.*Tests run: \([0-9]*\),.*/\1/p' <<<"$summary" | head -1)
    skips=$(sed -n 's/.*Skipped: \([0-9]*\).*/\1/p' <<<"$summary" | head -1)
    # 「全被 skip」与「一个用例都没跑」都不能算 PASS：它们没有证明任何事（NA 不是 PASS）
    if [[ -z "$total" || "$total" == "0" || "${total:-0}" == "${skips:-0}" ]]; then
      printf 'SKIP %-64s %s\n' "$cls" "${summary:-无用例执行（缺外部依赖）}"
      skipped+=("$cls")
    else
      printf 'PASS %-64s %s\n' "$cls" "$summary"
      ran=$((ran + 1))
    fi
  else
    summary=$(grep -E "^\[ERROR\] Tests run:|^\[ERROR\]   " "$log" | head -3 | tr '\n' ' ')
    printf 'FAIL %-64s %s\n' "$cls" "$summary"
    failed+=("$cls")
  fi
done

echo
echo "真正执行了用例：$ran 类；因缺外部依赖跳过：${#skipped[@]} 类；失败：${#failed[@]} 类"
if [[ ${#skipped[@]} -gt 0 ]]; then
  echo "跳过的类没有证明力（本机无 MySQL / 需真实环境），不要当成 PASS："
  printf '  - %s\n' "${skipped[@]}"
fi

if [[ ${#failed[@]} -gt 0 ]]; then
  echo "有 ${#failed[@]} 个测试类单独运行失败："
  printf '  - %s\n' "${failed[@]}"
  echo "这些类依赖执行顺序或外部环境，必须修掉（参见 BUG-017）。"
  exit 1
fi

echo "已执行的 $ran 个测试类均可独立运行"
