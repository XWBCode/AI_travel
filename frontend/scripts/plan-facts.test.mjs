/**
 * 「待核实」分类明细的纯函数测试（BUG-004）。
 *
 * 这段逻辑不产生业务数据，但它决定了用户看到的第一句话是「部分信息待核实」
 * 还是「74 个字段待核实」。所以三类情况必须钉住：
 *
 * 1. 计数与分组要和计划结构对得上（行程 / 交通 / 住宿 / 预算 / 其他）；
 * 2. 例子要能读出人话（「宽窄巷子 · 门票」而不是「days/0/items/0/ticketPrice」）；
 * 3. 结构里出现没见过的字段时不能漏算，也不能把非事实字段算进去。
 *
 * 运行：npm run test:plan-facts
 */
import { groupUnverified, unverifiedCategoryText } from '../src/utils/planFacts.ts'

let failures = 0

function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  if (!ok) failures++
  console.log(
    `${ok ? 'PASS' : 'FAIL'} ${name}` +
      (ok ? '' : ` -> ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`),
  )
}

/** L0 的事实字段：value 恒 null、display 恒「待核实」 */
function fact() {
  return { value: null, display: '待核实', source: 'NONE', verified: false, fetchedAt: null, expiresAt: null }
}

const PLAN = {
  schemaVersion: '1.0',
  title: '成都三日游',
  status: 'DRAFT',
  basic: { destination: '成都', days: 3 },
  days: [
    {
      dayIndex: 1,
      city: '成都',
      items: [
        { itemOrder: 1, title: '宽窄巷子', duration: fact(), openHours: fact(), ticketPrice: fact() },
        { itemOrder: 2, title: '武侯祠', duration: fact(), openHours: fact(), ticketPrice: fact() },
      ],
    },
    {
      dayIndex: 2,
      city: '成都',
      items: [{ itemOrder: 1, title: '金沙遗址博物馆', duration: fact(), ticketPrice: fact() }],
    },
  ],
  budget: {
    items: [
      { category: 'TRANSPORT', label: '往返与城际交通', amount: fact() },
      { category: 'FOOD', label: '餐饮', amount: fact() },
    ],
  },
  aiNote: '门票与开放时间请以景区公告为准',
}

const summary = groupUnverified(PLAN)

check('总数与事实字段个数一致', summary.total, 3 + 3 + 2 + 2)
check('分组顺序：行程在前、预算在后', summary.groups.map((g) => g.key), ['itinerary', 'budget'])
check('行程类计数', summary.groups[0].count, 8)
check('预算类计数', summary.groups[1].count, 2)
check('例子读得出人话', summary.groups[0].subjects[0], '宽窄巷子')
check('预算例子用分类名', summary.groups[1].subjects[0], '往返与城际交通')
check('每类最多三个例子', summary.groups[0].subjects.length, 3)
// 去重：三个行程项 = 三处安排，不是 8 条字段流水
check('行程类按对象去重', summary.groups[0].subjectCount, 3)
check('预算类按对象去重', summary.groups[1].subjectCount, 2)
check('分类文案用顿号连接', unverifiedCategoryText(summary), '行程安排 · 预算')

// ---------- 结构边界 ----------

check('空计划不炸', groupUnverified(null), { total: 0, groups: [] })
check('没有事实字段就没有分组', groupUnverified({ title: 'x', days: [] }), { total: 0, groups: [] })
check('非事实字段不算（缺 verified）', groupUnverified({ days: [{ items: [{ duration: { display: '待核实' } }] }] }).total, 0)

// 契约以后新增事实字段（例如天气），必须落在「其他」而不是被静默丢掉
const withWeather = groupUnverified({ weather: { ...fact(), display: '待核实' }, days: PLAN.days })
check('未知位置的事实字段进「其他」', withWeather.groups.map((g) => g.label), ['行程安排', '其他'])
check('未知位置也计入总数', withWeather.total, 8 + 1)

// 交通：例子应当是「成都 → 乐山」，而不是空字符串
const withTransport = groupUnverified({
  transportSegments: [{ scope: 'INTER_CITY', fromCity: '成都', toCity: '乐山', duration: fact() }],
})
check('交通分组带起终点', withTransport.groups[0].subjects[0], '成都 → 乐山')

if (failures > 0) {
  console.error(`\n${failures} 项失败`)
  process.exit(1)
}
console.log('\n全部通过')
