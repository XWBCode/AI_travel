/**
 * 解析一个 SSE 帧。
 *
 * 单独抽出来的原因：后端每 15s 会发一条注释行 `:ping` 来防止反代掐断长连接（R7）。
 * 按 SSE 规范，注释不进 onmessage，因此这里必须「没有 data 就当没有这回事」。
 * 这条规则以前藏在 dispatch 里，只能靠肉眼保证；抽出来才能被测试钉住——
 * 一旦哪天有人「顺手」把注释当成事件，页面会多出一堆空消息。
 */
export function parseFrame(chunk: string): { event: string; data: string } | null {
  let event = 'message'
  const dataLines: string[] = []
  for (const line of chunk.split('\n')) {
    if (line.startsWith('event:')) {
      event = line.slice(6).trim()
    } else if (line.startsWith('data:')) {
      dataLines.push(line.slice(5).trim())
    }
  }
  if (dataLines.length === 0) return null
  return { event, data: dataLines.join('\n') }
}
