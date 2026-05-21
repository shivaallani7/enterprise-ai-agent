import React, { useEffect, useState } from 'react'
import {
  LineChart, Line, XAxis, YAxis, CartesianGrid, Tooltip, Legend, ResponsiveContainer,
} from 'recharts'
import { api } from '../lib/api'

interface TrendPoint {
  date: string
  satisfaction: number
  total: number
  up: number
  down: number
}

export function Dashboard() {
  const [data, setData] = useState<TrendPoint[]>([])
  const [days, setDays] = useState(30)
  const [loading, setLoading] = useState(true)
  const [error, setError] = useState<string | null>(null)

  useEffect(() => {
    setLoading(true)
    setError(null)
    api.getFeedbackTrends(days)
      .then((res) => {
        const formatted = res.trend.map((p: TrendPoint) => ({
          ...p,
          date: new Date(Number(p.date) * 1000).toLocaleDateString('en-US', { month: 'short', day: 'numeric' }),
        }))
        setData(formatted)
      })
      .catch((err) => setError(err instanceof Error ? err.message : 'Failed to load trends'))
      .finally(() => setLoading(false))
  }, [days])

  return (
    <div style={styles.root}>
      <div style={styles.header}>
        <h2 style={styles.title}>RAGAS Feedback Dashboard</h2>
        <select
          style={styles.select}
          value={days}
          onChange={(e) => setDays(Number(e.target.value))}
        >
          <option value={7}>Last 7 days</option>
          <option value={30}>Last 30 days</option>
          <option value={90}>Last 90 days</option>
        </select>
      </div>

      {loading ? (
        <div style={styles.loading}>Loading trends…</div>
      ) : error ? (
        <div style={styles.errorBox}>{error}</div>
      ) : data.length === 0 ? (
        <div style={styles.empty}>No feedback data yet. Start rating responses!</div>
      ) : (
        <>
          <SummaryCards data={data} />
          <div style={styles.chartWrap}>
            <h3 style={styles.chartTitle}>User Satisfaction Over Time</h3>
            <ResponsiveContainer width="100%" height={300}>
              <LineChart data={data} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis dataKey="date" tick={{ fontSize: 12, fill: 'var(--text-muted)' }} />
                <YAxis domain={[0, 100]} tick={{ fontSize: 12, fill: 'var(--text-muted)' }} />
                <Tooltip
                  contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: '8px' }}
                  labelStyle={{ color: 'var(--text)' }}
                />
                <Legend />
                <Line
                  type="monotone"
                  dataKey="satisfaction"
                  stroke="var(--primary)"
                  strokeWidth={2}
                  dot={false}
                  name="Satisfaction %"
                />
              </LineChart>
            </ResponsiveContainer>
          </div>

          <div style={styles.chartWrap}>
            <h3 style={styles.chartTitle}>Response Volume</h3>
            <ResponsiveContainer width="100%" height={200}>
              <LineChart data={data} margin={{ top: 5, right: 20, left: 0, bottom: 5 }}>
                <CartesianGrid strokeDasharray="3 3" stroke="var(--border)" />
                <XAxis dataKey="date" tick={{ fontSize: 12, fill: 'var(--text-muted)' }} />
                <YAxis tick={{ fontSize: 12, fill: 'var(--text-muted)' }} />
                <Tooltip
                  contentStyle={{ background: 'var(--surface-2)', border: '1px solid var(--border)', borderRadius: '8px' }}
                />
                <Legend />
                <Line type="monotone" dataKey="up" stroke="var(--success)" strokeWidth={2} dot={false} name="Helpful" />
                <Line type="monotone" dataKey="down" stroke="var(--danger)" strokeWidth={2} dot={false} name="Not helpful" />
              </LineChart>
            </ResponsiveContainer>
          </div>
        </>
      )}
    </div>
  )
}

function SummaryCards({ data }: { data: TrendPoint[] }) {
  const totalUp = data.reduce((s, d) => s + d.up, 0)
  const totalDown = data.reduce((s, d) => s + d.down, 0)
  const total = totalUp + totalDown
  const avgSatisfaction = total > 0 ? Math.round((totalUp / total) * 100) : 0

  return (
    <div style={styles.cards}>
      <StatCard label="Avg Satisfaction" value={`${avgSatisfaction}%`} color="var(--primary)" />
      <StatCard label="Total Responses Rated" value={String(total)} color="var(--text)" />
      <StatCard label="Helpful" value={String(totalUp)} color="var(--success)" />
      <StatCard label="Not Helpful" value={String(totalDown)} color="var(--danger)" />
    </div>
  )
}

function StatCard({ label, value, color }: { label: string; value: string; color: string }) {
  return (
    <div style={styles.card}>
      <div style={{ ...styles.cardValue, color }}>{value}</div>
      <div style={styles.cardLabel}>{label}</div>
    </div>
  )
}

const styles: Record<string, React.CSSProperties> = {
  root: {
    padding: '24px',
    overflowY: 'auto',
    height: '100%',
  },
  header: {
    display: 'flex',
    justifyContent: 'space-between',
    alignItems: 'center',
    marginBottom: '24px',
  },
  title: {
    fontSize: '18px',
    fontWeight: 700,
  },
  select: {
    padding: '6px 12px',
    borderRadius: 'var(--radius)',
    fontSize: '13px',
    width: 'auto',
  },
  loading: {
    color: 'var(--text-muted)',
    padding: '40px',
    textAlign: 'center',
  },
  empty: {
    color: 'var(--text-muted)',
    padding: '40px',
    textAlign: 'center',
  },
  errorBox: {
    color: 'var(--danger)',
    fontSize: '13px',
    padding: '16px',
    background: 'rgba(239,68,68,0.08)',
    borderRadius: 'var(--radius)',
    border: '1px solid rgba(239,68,68,0.2)',
  },
  cards: {
    display: 'grid',
    gridTemplateColumns: 'repeat(auto-fit, minmax(140px, 1fr))',
    gap: '16px',
    marginBottom: '24px',
  },
  card: {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius)',
    padding: '16px',
    textAlign: 'center',
  },
  cardValue: {
    fontSize: '28px',
    fontWeight: 700,
    lineHeight: 1.2,
  },
  cardLabel: {
    fontSize: '12px',
    color: 'var(--text-muted)',
    marginTop: '4px',
  },
  chartWrap: {
    background: 'var(--surface)',
    border: '1px solid var(--border)',
    borderRadius: 'var(--radius)',
    padding: '20px',
    marginBottom: '24px',
  },
  chartTitle: {
    fontSize: '14px',
    fontWeight: 600,
    marginBottom: '16px',
    color: 'var(--text-muted)',
  },
}
