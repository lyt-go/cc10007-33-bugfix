/** 列表/详情共用的读取口径：旧响应不能覆盖新响应，同一业务编号只保留一行。

策略库列表页与实时决策详情页都用这一份合并规则，避免同一根因在一个页面表现为丢行、
在另一个页面表现为重复行。
 */
export type Row = Record<string, string | number | boolean | null>

export function mergeRows<T extends Row>(rows: T[], keyField: string): T[] {
  const merged: T[] = []
  const index = new Map<string, number>()
  rows.forEach((row) => {
    const key = String(row[keyField] ?? row.id ?? '').trim()
    if (!key) return
    const seen = index.get(key)
    if (seen === undefined) {
      index.set(key, merged.length)
      merged.push({ ...row })
      return
    }
    // 同一编号出现多次：字段合并成一行，保留最新非空值，不再重复渲染。
    merged[seen] = { ...merged[seen], ...row }
  })
  return merged
}

/** 单调递增的请求序号：只接受最后一次请求的结果，过期响应直接丢弃。 */
export function nextTicket(counter: { current: number }): number {
  counter.current += 1
  return counter.current
}

export function isLatest(counter: { current: number }, ticket: number): boolean {
  return counter.current === ticket
}
