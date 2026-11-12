/**
 * 版本回滚的纯函数测试。
 *
 * 为什么这些规则要单独测：它们决定「按钮出不出现」和「弹窗敢不敢点」，
 * 靠截图只能看到某一次的结果，而版本状态有 5 种 × 版本号大小关系两种组合。
 *
 * 运行：npm run test:rollback
 */
import {
  canRollback,
  canRollbackTo,
  rollbackConfirmText,
  rollbackSuccessText,
  versionLabel,
} from '../src/utils/rollback.ts'

let failures = 0

function check(name, actual, expected) {
  const ok = actual === expected
  if (!ok) failures++
  console.log(
    `${ok ? 'PASS' : 'FAIL'} ${name}` +
      (ok ? '' : ` -> ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`),
  )
}

// ---------- 终态判断 ----------

check('草稿可回滚', canRollback('DRAFT'), true)
check('已确认可回滚', canRollback('CONFIRMED'), true)
check('进行中可回滚', canRollback('IN_PROGRESS'), true)
check('已完成不可回滚', canRollback('COMPLETED'), false)
check('已取消不可回滚', canRollback('CANCELLED'), false)
check('未知状态按可回滚处理（交给后端拒）', canRollback('SOMETHING'), true)

// ---------- 目标版本是否可点 ----------

check('回滚到旧版本', canRollbackTo(0, 'DRAFT', 3), true)
check('回滚到当前版本 → 不给按钮', canRollbackTo(3, 'DRAFT', 3), false)
check('终态即使是旧版本也不给按钮', canRollbackTo(0, 'COMPLETED', 3), false)
check('终态的当前版本', canRollbackTo(3, 'CANCELLED', 3), false)

// ---------- 确认文案 ----------

const noProgress = rollbackConfirmText(0, 2, 0)
check('说清恢复目标', noProgress.includes('v0'), true)
check('说清当前版本', noProgress.includes('当前是 v2'), true)
check('说清回滚后会生成新版本（历史不丢）', noProgress.includes('v3'), true)
check('没有勾选时不提进度', noProgress.includes('清空'), false)

const withProgress = rollbackConfirmText(1, 4, 3)
check('有勾选时说明会清空几项', withProgress.includes('3 项'), true)
check('有勾选时说明原因', withProgress.includes('重建'), true)
check('多行文案便于逐段渲染', withProgress.split('\n').length, 3)

// ---------- 成功提示 ----------

check('没清进度时只报版本', rollbackSuccessText(1, 3, 0), '已恢复到 v1（当前 v3）')
check(
  '清了进度要如实报数',
  rollbackSuccessText(1, 3, 2),
  '已恢复到 v1（当前 v3），同时清空了 2 项勾选记录',
)

// ---------- 版本说明 ----------

check('v0 无摘要时用默认说明', versionLabel({ versionNo: 0 }), 'AI 生成初稿')
check('v0 有摘要时用摘要', versionLabel({ versionNo: 0, changeSummary: 'AI 生成初稿' }), 'AI 生成初稿')
check('v2 有摘要时用摘要', versionLabel({ versionNo: 2, changeSummary: '回滚到 v0' }), '回滚到 v0')
check('v2 无摘要时兜底', versionLabel({ versionNo: 2 }), '快照')

console.log(failures === 0 ? '\n全部通过' : `\n${failures} 项失败`)
process.exit(failures === 0 ? 0 : 1)
