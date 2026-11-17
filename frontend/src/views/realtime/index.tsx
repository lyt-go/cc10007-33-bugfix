import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { request } from '@/api/client'
import { isLatest, mergeRows, nextTicket } from '@/api/rows'

const ENDPOINT = '/api/realtime'
const columns = [
  "规则编号",
  "触发事件",
  "决策条件",
  "执行动作",
  "冷却时间秒",
  "优先级",
  "分流比例",
  "生效时段",
  "关联策略",
  "裁决状态",
  "当前执行动作"
]
const allowedActions: Record<string, string[]> = {
  "草稿": ["提交规则评审"],
  "规则评审中": ["评审通过"],
  "评审通过": ["生效规则"],
  "已生效": ["暂停规则"],
  "待重新裁决": ["提交规则评审", "暂停规则"],
  "已暂停": ["提交规则评审"]
}
const stats = [
  {
    "label": "决策规则总数",
    "value": 0
  },
  {
    "label": "已生效规则",
    "value": 0
  },
  {
    "label": "评审中规则",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

/** 列表筛选口径：编号列走后端 keyword，其余列在前端按当前页过滤，避免无效参数被静默忽略。 */
function matches(row: Row, filters: Record<string, string>): boolean {
  return Object.entries(filters).every(([field, value]) => {
    if (!value) return true
    if (field === "规则编号") return true
    return String(row[field] ?? '').includes(value)
  })
}

function summarize(rows: Row[], total: number) {
  return stats.map((item) => {
    if (item.label === "决策规则总数") return { ...item, value: total }
    if (item.label === "已生效规则") {
      return { ...item, value: rows.filter((row) => row.status === "已生效").length }
    }
    return { ...item, value: rows.filter((row) => row.status === "规则评审中").length }
  })
}

export default function RealtimeView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [detail, setDetail] = useState<Record<string, unknown> | null>(null)
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])
  const ticket = useRef(0)
  const visibleRows = useMemo(
    () => mergeRows(rows, "规则编号").filter((row) => matches(row, filters)),
    [rows, filters],
  )
  const summary = useMemo(() => summarize(rows, total), [rows, total])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const keyword = (filters["规则编号"] ?? '').trim()
    const query = keyword ? `?keyword=${encodeURIComponent(keyword)}` : ''
    const current = nextTicket(ticket)
    try {
      const response = await request(`${ENDPOINT}${query}`)
      if (!response.ok) {
        throw new Error('决策规则列表读取失败')
      }
      const payload = await response.json()
      if (!isLatest(ticket, current)) return
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      if (!isLatest(ticket, current)) return
      setErrorMessage(error instanceof Error ? error.message : '实时决策列表读取失败')
    }
  }, [filters])

  useEffect(() => {
    void reload()
  }, [reload])

  async function runAction(action: string, row: Row) {
    setErrorMessage('')
    setNotice('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/actions`, {
        method: 'POST',
        body: JSON.stringify({ action }),
      })
      const payload = await response.json().catch(() => ({}))
      if (!response.ok || payload.ok === false) {
        throw new Error(payload.message ?? payload.detail ?? '实时决策动作未生效，请稍后重试')
      }
      setNotice(payload.message ?? `${action}已完成`)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '实时决策操作失败')
    }
  }

  async function openDetail(row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/detail`)
      if (!response.ok) {
        throw new Error('实时决策详情读取失败')
      }
      setDetail(await response.json())
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '实时决策详情读取失败')
    }
  }

  return (
    <section className="page" data-module="realtime">
      <header className="page-head">
        <div>
          <h2>实时决策管理</h2>
          <p className="page-desc">配置实时运营决策规则，基于用户实时行为触发个性化动作。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('决策规则登记入口尚未接入审批流')}>
            登记决策规则
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出实时决策清单
          </button>
        </div>
      </header>

      <div className="stat-row">
        {summary.map((item) => (
          <article key={item.label} className="stat-card">
            <span className="stat-label">{item.label}</span>
            <strong className="stat-value">{item.value}</strong>
          </article>
        ))}
      </div>

      <form className="filter-bar" onSubmit={(event) => { event.preventDefault(); void reload() }}>
        {filterFields.map((field) => (
          <label key={field} className="filter-item">
            <span>{field}</span>
            <input
              value={filters[field] ?? ''}
              placeholder={`按${field}检索`}
              onChange={(event) => setFilters((prev) => ({ ...prev, [field]: event.target.value }))}
            />
          </label>
        ))}
        <button className="btn" type="submit">查询</button>
        <button className="btn ghost" type="button" onClick={() => setFilters({})}>重置条件</button>
      </form>

      <table className="data-table">
        <thead>
          <tr>
            {columns.map((column) => <th key={column}>{column}</th>)}
            <th>可执行动作</th>
          </tr>
        </thead>
        <tbody>
          {visibleRows.map((row) => (
            <tr key={String(row.id ?? row["规则编号"])}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                {(allowedActions[String(row.status)] ?? []).map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
                <button className="link" type="button" onClick={() => void openDetail(row)}>
                  决策详情
                </button>
              </td>
            </tr>
          ))}
          {visibleRows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无实时决策数据，可先登记决策规则</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>显示 {visibleRows.length} / 共 {total} 条实时决策记录</span>
        {notice && <span>{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>

      {detail && (
        <section className="detail-panel">
          <header className="page-head">
            <h3>决策详情：{String((detail["rule"] as Record<string, unknown>)["规则编号"])}</h3>
            <button className="btn ghost" type="button" onClick={() => setDetail(null)}>关闭</button>
          </header>
          <table className="data-table">
            <tbody>
              <tr><th>规则状态</th><td>{String((detail["arbitration"] as Record<string, unknown>)["规则状态"])}</td></tr>
              <tr><th>关联策略</th><td>{String((detail["arbitration"] as Record<string, unknown>)["关联策略"])}</td></tr>
              <tr><th>裁决状态</th><td>{String((detail["arbitration"] as Record<string, unknown>)["裁决状态"])}</td></tr>
              <tr><th>最终动作</th><td>{String((detail["arbitration"] as Record<string, unknown>)["最终动作"] || '—')}</td></tr>
              <tr><th>动作来源</th><td>{String((detail["arbitration"] as Record<string, unknown>)["动作来源"] || '—')}</td></tr>
              <tr><th>裁决依据</th><td>{String((detail["arbitration"] as Record<string, unknown>)["裁决依据"] || '—')}</td></tr>
              <tr><th>待裁决原因</th><td>{String((detail["arbitration"] as Record<string, unknown>)["待裁决原因"] || '—')}</td></tr>
            </tbody>
          </table>
          <table className="data-table">
            <thead>
              <tr><th>明细编号</th><th>策略编号</th><th>命中动作</th><th>命中类型</th><th>执行状态</th><th>命中时间</th></tr>
            </thead>
            <tbody>
              {(detail["executions"] as {
                明细编号: string; 策略编号: string; 命中动作: string; 命中类型: string; 执行状态: string; 命中时间: string
              }[]).map((item) => (
                <tr key={item.明细编号}>
                  <td>{item.明细编号}</td>
                  <td>{item.策略编号}</td>
                  <td>{item.命中动作}</td>
                  <td>{item.命中类型}</td>
                  <td>{item.执行状态}</td>
                  <td>{item.命中时间}</td>
                </tr>
              ))}
              {(detail["executions"] as unknown[]).length === 0 && (
                <tr><td className="empty-state" colSpan={6}>暂无执行明细</td></tr>
              )}
            </tbody>
          </table>
        </section>
      )}
    </section>
  )
}
