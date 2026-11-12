/**
 * 追问补答拼接的回归测试（纯函数，不需要浏览器）。
 *
 * 背景：追问按钮发出去的不是裸值（「成都」），而是补答短语（「去成都玩」）。
 * 因为后端按整句话抽取槽位，「帮我规划三天，成都」抽不出目的地，
 * 换成「帮我规划三天，去成都玩」才能命中。
 *
 * 运行：npm run test:followup
 */
import { buildFollowUpReply, isFollowUpComplete, parseFollowUps } from '../src/api/followup.ts'

let failures = 0

function check(name, actual, expected) {
  const ok = JSON.stringify(actual) === JSON.stringify(expected)
  if (!ok) failures++
  console.log(
    `${ok ? 'PASS' : 'FAIL'} ${name}` + (ok ? '' : ` -> ${JSON.stringify(actual)} != ${JSON.stringify(expected)}`),
  )
}

// 1. 单槽位：原话 + 补答
check(
  '缺天数',
  buildFollowUpReply('我想去成都', { days: '玩3天' }, ['days']),
  '我想去成都，玩3天',
)

// 2. 两槽位：按字段顺序拼
check(
  '缺两个',
  buildFollowUpReply('帮我规划一下行程', { destination: '去西安玩', days: '玩4天' }, [
    'destination',
    'days',
  ]),
  '帮我规划一下行程，去西安玩，玩4天',
)

// 3. 原话以标点结尾时不产生「。，」
check('去掉尾部标点', buildFollowUpReply('我想去成都。', { days: '玩2天' }, ['days']), '我想去成都，玩2天')

// 4. 只选了一部分时，未选的字段不产生空段
check(
  '部分选择',
  buildFollowUpReply('帮我规划一下行程', { destination: '去杭州玩' }, ['destination', 'days']),
  '帮我规划一下行程，去杭州玩',
)

// 5. 没选任何值：原样返回（用户可能自己打字补充）
check('无选择', buildFollowUpReply('我想去成都', {}, ['days']), '我想去成都')

// 6. 空原话：只返回补答，不产生前导逗号
check('空原话', buildFollowUpReply('   ', { days: '玩3天' }, ['days']), '玩3天')

// 7. 完成判定
check('全部选完', isFollowUpComplete({ destination: '去成都玩', days: '玩3天' }, ['destination', 'days']), true)
check('只选一半', isFollowUpComplete({ destination: '去成都玩' }, ['destination', 'days']), false)
check('空字段不启动', isFollowUpComplete({}, []), false)
check('空白值不算选中', isFollowUpComplete({ days: '  ' }, ['days']), false)

// 8. 服务端结构解析：正常结构
check(
  '解析正常结构',
  parseFollowUps([
    { field: 'days', question: '大概玩几天？', options: [{ label: '3 天', value: '玩3天' }] },
  ]),
  [{ field: 'days', question: '大概玩几天？', options: [{ label: '3 天', value: '玩3天' }] }],
)

// 9. 缺 options 的追问整组丢弃（按钮点不动，不如让用户自己打字）
check('缺 options 丢弃', parseFollowUps([{ field: 'days', question: '大概玩几天？' }]), [])

// 10. 旧格式（字符串数组）不应崩，直接忽略
check('旧格式忽略', parseFollowUps(['大概玩几天？']), [])

// 11. 脏选项被过滤，剩下合法的仍可用
check(
  '过滤脏选项',
  parseFollowUps([
    {
      field: 'days',
      question: '大概玩几天？',
      options: [{ label: '3 天', value: '玩3天' }, { label: '', value: 'x' }, null, { label: 'y' }],
    },
  ]),
  [{ field: 'days', question: '大概玩几天？', options: [{ label: '3 天', value: '玩3天' }] }],
)

// 12. 非数组输入
check('非数组', parseFollowUps(undefined), [])

console.log(failures === 0 ? '\n全部通过' : `\n${failures} 项失败`)
process.exit(failures === 0 ? 0 : 1)
