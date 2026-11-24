/**
 * 天气展示口径的纯函数测试（M3-1，W-3 / W-8 的前端侧）。
 *
 * 这段逻辑不产生任何数值，但它决定了三件容易出错的事：
 *
 * 1. **降级文案要能区分「我能做点什么」和「只能等」**——
 *    「没有出发日期」可以补，「超出预报范围」只能等，混成一句用户会白找半天。
 * 2. **未核实字段一律「暂无数据」**，不能留空让人误以为是 0 度或晴天。
 * 3. **署名与时效必须出现**：CC BY 4.0 要求署名；`stale=true` 不标出来就是拿旧数据冒充新数据。
 *
 * 运行：npm run test:weather
 */
import {
  ATTRIBUTION_FALLBACK,
  attributionText,
  attributionUrl,
  canFixByUser,
  cityNote,
  fieldText,
  reasonText,
  relativeTime,
  sourceLine,
  staleReasonText,
  staleText,
  toRows,
} from '../src/utils/weather.ts'

let failures = 0

function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  if (!ok) failures++
  console.log(
    `${ok ? 'PASS' : 'FAIL'} ${name}` +
      (ok ? '' : ` -> ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`),
  )
}

function fact(display, verified = true) {
  return {
    value: verified ? 1 : null,
    display,
    source: verified ? 'TOOL' : 'NONE',
    verified,
    fetchedAt: verified ? '2026-11-23T22:40:00+08:00' : null,
    expiresAt: verified ? '2026-11-23T23:10:00+08:00' : null,
    note: '',
  }
}

const ATTR = 'Weather data by Open-Meteo.com (CC BY 4.0)'

// ---------------- 降级文案 ----------------
check('没出发日期：优先用服务端文案', reasonText('NO_START_DATE', '补充出发日期后可查看天气'), '补充出发日期后可查看天气')
check('没出发日期：无 message 时用本地映射', reasonText('NO_START_DATE'), '补充出发日期后可查看天气')
check('超出窗口：与「没日期」是不同的提示', reasonText('DATE_OUT_OF_RANGE'), '出发日期超出预报范围，临近再看')
check('未知原因落到兜底句而不是空白', reasonText('SOMETHING_NEW'), '天气暂不可用')
check('原因为空也落到兜底句', reasonText(null, null), '天气暂不可用')
check('只有「缺日期」给用户操作入口', [canFixByUser('NO_START_DATE'), canFixByUser('DATE_OUT_OF_RANGE')], [true, false])

// 熔断（CIRCUIT_OPEN）与上游失败（UPSTREAM_ERROR）必须说两句不同的话：
// 前者是我们主动停的、会自己恢复；后者是上游真的坏了
check('熔断有独立文案，不落兜底句', reasonText('CIRCUIT_OPEN') !== '天气暂不可用', true)
check(
  '熔断文案与上游错误不同',
  reasonText('CIRCUIT_OPEN') !== reasonText('UPSTREAM_ERROR'),
  true,
)
check('熔断说明会自己恢复，用户不用改设置', reasonText('CIRCUIT_OPEN').includes('自动恢复'), true)
check('熔断不是用户能自己修的', canFixByUser('CIRCUIT_OPEN'), false)

// ---------------- 字段展示 ----------------
check('已核实照抄 display', fieldText(fact('12–16℃')), '12–16℃')
check('未核实一律「暂无数据」', fieldText(fact('待核实', false)), '暂无数据')
check('字段缺失也是「暂无数据」而不是空串', fieldText(null), '暂无数据')
check(
  'verifed=true 但 display 为空也不许留空',
  fieldText({ ...fact(''), display: '' }),
  '暂无数据',
)

// ---------------- 逐日渲染 ----------------
const OK = {
  available: true,
  source: 'OPEN_METEO',
  attribution: ATTR,
  fetchedAt: '2026-11-23T22:40:00+08:00',
  stale: false,
  note: null,
  days: [
    {
      dayIndex: 1,
      date: '2026-11-24',
      city: '云南',
      resolvedCity: '昆明',
      weather: fact('小毛毛雨'),
      temperature: fact('7–10℃'),
      precipitation: fact('降水概率 55%'),
      wind: fact('2级风'),
    },
    {
      dayIndex: 2,
      date: '2026-11-25',
      city: '云南',
      resolvedCity: '昆明',
      weather: fact('晴'),
      temperature: fact('9–16℃'),
      precipitation: fact('降水概率 0%'),
      wind: fact('3级风'),
    },
  ],
}

const rows = toRows(OK)
check('降级时没有行', toRows({ available: false, reason: 'NO_START_DATE', days: [] }), [])
check('每天一行，日期压成 MM-DD', rows.map((r) => [r.dayIndex, r.dayLabel]), [[1, '11-24'], [2, '11-25']])
check('城市显示计划里的写法（用户认得出）', rows[0].city, '云南')
check('四组字段原样照抄', [rows[0].weather, rows[0].temperature, rows[0].precipitation, rows[0].wind], [
  '小毛毛雨',
  '7–10℃',
  '降水概率 55%',
  '2级风',
])

const blankDay = {
  available: true,
  days: [{ dayIndex: 1, date: '2026-11-24', city: '成都', weather: fact('', false), temperature: fact('', false), precipitation: fact('', false), wind: fact('', false) }],
}
const blankRow = toRows(blankDay)[0]
check('整天空数据时 hasValue=false', blankRow.hasValue, false)
check('整天空数据时不显示任何数字', [blankRow.weather, blankRow.temperature, blankRow.wind], ['暂无数据', '暂无数据', '暂无数据'])

// ---------------- 署名 / 时效 / 旧值 ----------------
const NOW = new Date('2026-11-23T23:03:00+08:00')
check('署名必须出现', sourceLine(OK, NOW).includes('Open-Meteo'), true)
// 兜底署名不写死某一家：D-03 换源后「数据来源 Open-Meteo」就是一句假话
check('attribution 缺失时退回中性署名（不能因为后端没给就不署）', sourceLine({ ...OK, attribution: undefined }, NOW).includes(ATTRIBUTION_FALLBACK), true)
check('兜底署名不点名任何 provider', attributionText({ ...OK, attribution: '' }).includes('Open-Meteo'), false)
check('相对时效：23 分钟前', relativeTime('2026-11-23T22:40:00+08:00', NOW), '23 分钟前')
check('相对时效：1 分钟内算刚刚', relativeTime('2026-11-23T23:02:30+08:00', NOW), '刚刚')
check('相对时效：跨小时', relativeTime('2026-11-23T21:40:00+08:00', NOW), '1 小时前')
check('相对时效：跨天', relativeTime('2026-11-21T22:40:00+08:00', NOW), '2 天前')
check('没有时间就不显示这一段', sourceLine({ ...OK, fetchedAt: undefined }, NOW).includes('更新于'), false)
check('降级时没有署名行', sourceLine({ available: false, reason: 'NO_START_DATE', days: [] }, NOW), '')

// 归因链接：和风天气条款要求「和风天气 / QWeather + 超链」，只写文字不合规
check('和风天气 → 官方链接', attributionUrl('天气服务由和风天气驱动'), 'https://www.qweather.com')
check('QWeather 英文写法也能匹配', attributionUrl('QWeather'), 'https://www.qweather.com')
check('Open-Meteo（开发源）也能匹配', attributionUrl(ATTR), 'https://open-meteo.com')
check('认不出的归因不编链接', attributionUrl('某天气服务'), '')
check('归因为空不编链接', attributionUrl(''), '')
check('链接锚文本就是后端给的原文', attributionText({ ...OK, attribution: '天气服务由和风天气驱动' }), '天气服务由和风天气驱动')

check('旧值必须标出来', staleText({ ...OK, stale: true }).includes('上次'), true)
check('不是旧值就不加这句', staleText(OK), '')

// ---------------- 旧值 + 刷新失败原因 ----------------
//
// 「这是上次的数据」和「为什么没更新」是两件事。只说前者，用户分不出是上游坏了
// 还是我们自己把请求停了——那正是 M3-2 暂缓的理由（故障状态没闭环）。
check('旧值提示不再说"正在尝试更新"', staleText({ ...OK, stale: true }), '数据为上次成功获取的结果')
check(
  '旧值 + 熔断：说明是我们主动停的',
  staleReasonText({ ...OK, stale: true, reason: 'CIRCUIT_OPEN' }),
  '本次刷新失败：天气服务连续异常，已暂停刷新，稍后会自动恢复',
)
check(
  '旧值 + 真实上游故障：说明数据还在、只是不新',
  staleReasonText({ ...OK, stale: true, reason: 'UPSTREAM_ERROR' }),
  '本次刷新失败：天气服务暂时不可用，正在保留上次数据',
)
check(
  '两种失败在旧值场景下也必须说不同的话',
  staleReasonText({ ...OK, stale: true, reason: 'CIRCUIT_OPEN' }) !==
    staleReasonText({ ...OK, stale: true, reason: 'UPSTREAM_ERROR' }),
  true,
)
check('不是旧值就不解释刷新失败', staleReasonText({ ...OK, reason: 'CIRCUIT_OPEN' }), '')
check('旧值但没有原因：不硬编一句', staleReasonText({ ...OK, stale: true, reason: null }), '')
check(
  '原因只认结构化 reason，不从 note 里抠英文枚举',
  staleReasonText({
    ...OK,
    stale: true,
    reason: null,
    note: '数据为上次成功获取的结果（本次刷新失败：CIRCUIT_OPEN）',
  }),
  '',
)
check(
  'note 里写什么都不影响文案（防止有人回头去解析 note）',
  staleReasonText({ ...OK, stale: true, reason: 'CIRCUIT_OPEN', note: '本次刷新失败：UPSTREAM_ERROR' }),
  '本次刷新失败：天气服务连续异常，已暂停刷新，稍后会自动恢复',
)

// 城市别名说明：有就显示，刷新失败的说明不在城市说明里重复
check('别名说明会显示', cityNote({ ...OK, note: '按省会昆明代表云南' }), '按省会昆明代表云南')
check('刷新失败的说明不和城市说明混在一起', cityNote({ ...OK, note: '数据为上次成功获取的结果（本次刷新失败：UPSTREAM_ERROR）' }), '')
check('没有说明就不占位', cityNote({ ...OK, note: null }), '')

if (failures > 0) {
  console.error(`\n${failures} 项失败`)
  process.exit(1)
}
console.log('\n全部通过')
