import { useCallback, useEffect, useMemo, useRef, useState } from 'react'

import { request } from '@/api/client'
import { isLatest, mergeRows, nextTicket } from '@/api/rows'

const ENDPOINT = '/api/tacticlib'
const columns = [
  "策略编号",
  "策略名称",
  "策略类型",
  "触发条件",
  "执行动作",
  "生效周期",
  "策略评分",
  "使用次数"
]
const allowedActions: Record<string, string[]> = {
  "草稿": ["提交策略评审"],
  "策略评审中": ["发布策略"],
  "已发布": ["退役策略"],
  "已退役": []
}
const stats = [
  {
    "label": "运营策略总数",
    "value": 0
  },
  {
    "label": "已发布策略",
    "value": 0
  },
  {
    "label": "评审中策略",
    "value": 0
  }
]

type Row = Record<string, string | number | null>

/** 列表筛选口径：编号列走后端 keyword，其余列在前端按当前页过滤，避免无效参数被静默忽略。 */
function matches(row: Row, filters: Record<string, string>): boolean {
  return Object.entries(filters).every(([field, value]) => {
    if (!value) return true
    if (field === "策略编号") return true
    return String(row[field] ?? '').includes(value)
  })
}

function summarize(rows: Row[], total: number) {
  return stats.map((item) => {
    if (item.label === "运营策略总数") return { ...item, value: total }
    if (item.label === "已发布策略") {
      return { ...item, value: rows.filter((row) => row.status === "已发布").length }
    }
    return { ...item, value: rows.filter((row) => row.status === "策略评审中").length }
  })
}

export default function TacticlibView() {
  const [rows, setRows] = useState<Row[]>([])
  const [total, setTotal] = useState(0)
  const [errorMessage, setErrorMessage] = useState('')
  const [notice, setNotice] = useState('')
  const [reconcile, setReconcile] = useState<Record<string, unknown> | null>(null)
  const [filters, setFilters] = useState<Record<string, string>>({})
  const filterFields = useMemo(() => columns.slice(0, 3), [])
  const ticket = useRef(0)
  const visibleRows = useMemo(
    () => mergeRows(rows, "策略编号").filter((row) => matches(row, filters)),
    [rows, filters],
  )
  const summary = useMemo(() => summarize(rows, total), [rows, total])

  const reload = useCallback(async () => {
    setErrorMessage('')
    const keyword = (filters["策略编号"] ?? '').trim()
    const query = keyword ? `?keyword=${encodeURIComponent(keyword)}` : ''
    const current = nextTicket(ticket)
    try {
      const response = await request(`${ENDPOINT}${query}`)
      if (!response.ok) {
        throw new Error('运营策略列表读取失败')
      }
      const payload = await response.json()
      if (!isLatest(ticket, current)) return
      setRows(payload.items ?? [])
      setTotal(payload.total ?? 0)
    } catch (error) {
      if (!isLatest(ticket, current)) return
      setErrorMessage(error instanceof Error ? error.message : '策略库列表读取失败')
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
        throw new Error(payload.message ?? payload.detail ?? '策略库动作未生效，请稍后重试')
      }
      setNotice(payload.message ?? `${action}已完成`)
      await reload()
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '策略库操作失败')
    }
  }

  async function openReconcile(row: Row) {
    setErrorMessage('')
    try {
      const response = await request(`${ENDPOINT}/${row.id}/reconcile`)
      if (!response.ok) {
        throw new Error('批次核对数据读取失败')
      }
      setReconcile(await response.json())
    } catch (error) {
      setErrorMessage(error instanceof Error ? error.message : '批次核对读取失败')
    }
  }

  return (
    <section className="page" data-module="tacticlib">
      <header className="page-head">
        <div>
          <h2>策略库管理</h2>
          <p className="page-desc">维护可复用的运营策略库，按类型、触发条件和执行动作分类管理。</p>
        </div>
        <div className="page-actions">
          <button className="btn primary" type="button" onClick={() => setErrorMessage('运营策略登记入口尚未接入审批流')}>
            登记运营策略
          </button>
          <button className="btn" type="button" onClick={() => window.open(`${ENDPOINT}/export`, '_blank')}>
            导出策略库清单
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
            <tr key={String(row.id ?? row["策略编号"])}>
              {columns.map((column) => <td key={column}>{row[column] ?? '—'}</td>)}
              <td className="row-actions">
                {(allowedActions[String(row.status)] ?? []).map((action) => (
                  <button key={action} className="link" type="button" onClick={() => void runAction(action, row)}>
                    {action}
                  </button>
                ))}
                <button className="link" type="button" onClick={() => void openReconcile(row)}>
                  批次核对
                </button>
              </td>
            </tr>
          ))}
          {visibleRows.length === 0 && (
            <tr>
              <td className="empty-state" colSpan={columns.length + 1}>暂无策略库数据，可先登记运营策略</td>
            </tr>
          )}
        </tbody>
      </table>

      <footer className="page-foot">
        <span>显示 {visibleRows.length} / 共 {total} 条策略库记录</span>
        {notice && <span>{notice}</span>}
        {errorMessage && <span className="error-text">{errorMessage}</span>}
      </footer>

      {reconcile && (
        <section className="detail-panel">
          <header className="page-head">
            <h3>批次核对：{String(reconcile["核对对象"])}</h3>
            <button className="btn ghost" type="button" onClick={() => setReconcile(null)}>关闭</button>
          </header>
          <table className="data-table">
            <thead>
              <tr><th>序号</th><th>环节</th><th>对象</th><th>结果</th></tr>
            </thead>
            <tbody>
              {(reconcile["核对序列"] as { 序号: number; 环节: string; 对象: string; 结果: string }[]).map((step) => (
                <tr key={`${step.序号}-${step.环节}`}>
                  <td>{step.序号}</td>
                  <td>{step.环节}</td>
                  <td>{step.对象}</td>
                  <td>{step.结果}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <table className="data-table">
            <thead>
              <tr><th>规则编号</th><th>规则状态</th><th>裁决状态</th><th>最终动作</th><th>待裁决原因</th></tr>
            </thead>
            <tbody>
              {(reconcile["关联规则裁决"] as {
                规则编号: string; 规则状态: string; 裁决状态: string; 最终动作: string; 待裁决原因: string
              }[]).map((item) => (
                <tr key={item.规则编号}>
                  <td>{item.规则编号}</td>
                  <td>{item.规则状态}</td>
                  <td>{item.裁决状态}</td>
                  <td>{item.最终动作 || '—'}</td>
                  <td>{item.待裁决原因 || '—'}</td>
                </tr>
              ))}
            </tbody>
          </table>
          <table className="data-table">
            <thead>
              <tr><th>明细编号</th><th>规则编号</th><th>命中动作</th><th>命中类型</th><th>执行状态</th></tr>
            </thead>
            <tbody>
              {(reconcile["执行明细"] as {
                明细编号: string; 规则编号: string; 命中动作: string; 命中类型: string; 执行状态: string
              }[]).map((item) => (
                <tr key={item.明细编号}>
                  <td>{item.明细编号}</td>
                  <td>{item.规则编号}</td>
                  <td>{item.命中动作}</td>
                  <td>{item.命中类型}</td>
                  <td>{item.执行状态}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}
    </section>
  )
}
