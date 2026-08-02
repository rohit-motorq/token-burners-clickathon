import { useState, useEffect, useRef, useCallback } from "react";
import {
  AreaChart, Area, BarChart, Bar, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  Brush, PieChart, Pie, Cell, Legend
} from "recharts";
import {
  MessageSquare, Send, Users, Activity, TrendingUp, TrendingDown,
  Globe, Clock, Film, Trash2, Check, ChevronDown, ChevronUp,
  Zap, BarChart3, Eye, MapPin, Sparkles, ArrowUpRight, ArrowDownRight,
  Bot, User
} from "lucide-react";

// --- Color tokens ---
const C = {
  blue: "#2a78d6", orange: "#eb6834", aqua: "#1baf7a",
  yellow: "#eda100", magenta: "#e87ba4", green: "#0ca30c",
  red: "#d03b3b", violet: "#4a3aa7",
  bg: "#fcfcfb", card: "#f7f7f6", border: "rgba(11,11,11,0.10)",
  textP: "#0b0b0b", textS: "#52514e", textM: "#898781",
  grid: "#e1e0d9"
};

// --- Seed data generators ---
function genTimeSeries(points, base, variance) {
  const now = new Date();
  return Array.from({ length: points }, (_, i) => {
    const t = new Date(now.getTime() - (points - 1 - i) * 60000);
    return {
      time: `${String(t.getHours()).padStart(2,"0")}:${String(t.getMinutes()).padStart(2,"0")}`,
      value: Math.max(0, Math.round(base + (Math.random() - 0.45) * variance)),
    };
  });
}

const COUNTRIES = [
  { name: "United States", code: "US", viewers: 42100, pct: 31 },
  { name: "India", code: "IN", viewers: 28400, pct: 21 },
  { name: "Brazil", code: "BR", viewers: 14200, pct: 10 },
  { name: "Germany", code: "DE", viewers: 11300, pct: 8 },
  { name: "Japan", code: "JP", viewers: 9800, pct: 7 },
  { name: "United Kingdom", code: "GB", viewers: 8600, pct: 6 },
  { name: "France", code: "FR", viewers: 7100, pct: 5 },
  { name: "Others", code: "--", viewers: 16500, pct: 12 },
];

const CONTENT_DATA = [
  { id: 1, title: "Breaking: Global Summit 2026", type: "News", views: 84200, trend: 12, country: "US", peakHour: "09:00", retention: 78, status: "keep" },
  { id: 2, title: "AI in Healthcare Deep Dive", type: "Documentary", views: 71300, trend: 24, country: "IN", peakHour: "20:00", retention: 82, status: "keep" },
  { id: 3, title: "Euro League Finals Replay", type: "Sports", views: 63100, trend: -8, country: "DE", peakHour: "21:00", retention: 65, status: "keep" },
  { id: 4, title: "Cooking Masterclass S3E12", type: "Lifestyle", views: 45600, trend: 5, country: "BR", peakHour: "18:00", retention: 71, status: "keep" },
  { id: 5, title: "Retro Gaming Marathon", type: "Entertainment", views: 38900, trend: -3, country: "US", peakHour: "22:00", retention: 58, status: "review" },
  { id: 6, title: "Quantum Computing 101", type: "Education", views: 32400, trend: 18, country: "JP", peakHour: "14:00", retention: 85, status: "keep" },
  { id: 7, title: "Morning Yoga Flow", type: "Wellness", views: 28100, trend: 1, country: "GB", peakHour: "07:00", retention: 44, status: "review" },
  { id: 8, title: "Vintage Car Restoration", type: "Hobby", views: 12300, trend: -22, country: "US", peakHour: "16:00", retention: 32, status: "remove" },
  { id: 9, title: "Old Movie Reviews (2019)", type: "Archive", views: 4800, trend: -41, country: "FR", peakHour: "23:00", retention: 18, status: "remove" },
  { id: 10, title: "Test Stream - Do Not Air", type: "Internal", views: 320, trend: -90, country: "--", peakHour: "--", retention: 5, status: "remove" },
];

const PEAK_HOURS = [
  { hour: "00", content: 1200, scaling: 800 }, { hour: "02", content: 800, scaling: 600 },
  { hour: "04", content: 600, scaling: 500 }, { hour: "06", content: 2100, scaling: 1200 },
  { hour: "08", content: 5800, scaling: 3400 }, { hour: "10", content: 7200, scaling: 4100 },
  { hour: "12", content: 6800, scaling: 3900 }, { hour: "14", content: 7500, scaling: 4300 },
  { hour: "16", content: 8100, scaling: 4800 }, { hour: "18", content: 9200, scaling: 5600 },
  { hour: "20", content: 9800, scaling: 6100 }, { hour: "22", content: 7400, scaling: 4200 },
];

const PIE_COLORS = [C.blue, C.aqua, C.orange, C.yellow, C.magenta, C.green, C.violet, C.red];

// --- Metric tile ---
function MetricTile({ label, value, delta, icon: Icon, color, data }) {
  const isUp = delta >= 0;
  return (
    <div style={{ background: C.card, borderRadius: 8, padding: "14px 16px", flex: 1, minWidth: 170 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
        <Icon size={14} color={C.textM} />
        <span style={{ fontSize: 12, color: C.textM }}>{label}</span>
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span style={{ fontSize: 32, fontWeight: 500, color: C.textP, fontFamily: "Georgia, serif", letterSpacing: "-0.02em" }}>
          {typeof value === "number" ? value.toLocaleString() : value}
        </span>
        <span style={{ fontSize: 12, fontWeight: 500, color: isUp ? C.green : C.red, display: "flex", alignItems: "center", gap: 2 }}>
          {isUp ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
          {Math.abs(delta)}%
        </span>
      </div>
      {data && (
        <div style={{ marginTop: 8, height: 28 }}>
          <ResponsiveContainer width="100%" height={28}>
            <AreaChart data={data.slice(-20)}>
              <Area type="monotone" dataKey="value" stroke={color} fill={color} fillOpacity={0.08} strokeWidth={1.5} dot={false} />
            </AreaChart>
          </ResponsiveContainer>
        </div>
      )}
    </div>
  );
}

// --- Chat panel (LibreChat-style) ---
function ChatPanel() {
  const [messages, setMessages] = useState([
    { role: "bot", text: "Welcome to the analytics assistant. Ask me about content performance, user trends, or scaling metrics." },
    { role: "bot", text: "Try: \"Which content should we remove?\" or \"Show peak hours for India\"" },
  ]);
  const [input, setInput] = useState("");
  const [loading, setLoading] = useState(false);
  const endRef = useRef(null);

  useEffect(() => { endRef.current?.scrollIntoView({ behavior: "smooth" }); }, [messages]);

  const handleSend = useCallback(async () => {
    const q = input.trim();
    if (!q || loading) return;
    setMessages(m => [...m, { role: "user", text: q }]);
    setInput("");
    setLoading(true);

    try {
      const res = await fetch("/v1/chat/completions", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          model: "concurrency-agent",
          messages: [{ role: "user", content: q }],
        }),
      });
      const data = await res.json();
      const text = data.choices?.[0]?.message?.content || "Sorry, I couldn't process that.";
      setMessages(m => [...m, { role: "bot", text }]);
    } catch {
      setMessages(m => [...m, { role: "bot", text: "Connection error. The AI assistant is unavailable right now." }]);
    }
    setLoading(false);
  }, [input, loading]);

  return (
    <div style={{ display: "flex", flexDirection: "column", height: "100%", background: C.bg }}>
      <div style={{ padding: "12px 16px", borderBottom: `0.5px solid ${C.border}`, display: "flex", alignItems: "center", gap: 8 }}>
        <Bot size={16} color={C.blue} />
        <span style={{ fontSize: 13, fontWeight: 500 }}>Analytics assistant</span>
        <span style={{ fontSize: 11, color: C.textM, marginLeft: "auto", background: C.card, padding: "2px 8px", borderRadius: 4 }}>AI-powered</span>
      </div>
      <div style={{ flex: 1, overflowY: "auto", padding: 12, display: "flex", flexDirection: "column", gap: 10 }}>
        {messages.map((m, i) => (
          <div key={i} style={{ display: "flex", gap: 8, alignSelf: m.role === "user" ? "flex-end" : "flex-start", maxWidth: "88%" }}>
            {m.role === "bot" && (
              <div style={{ width: 24, height: 24, borderRadius: "50%", background: `${C.blue}18`, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, marginTop: 2 }}>
                <Bot size={13} color={C.blue} />
              </div>
            )}
            <div style={{
              padding: "8px 12px", borderRadius: 10, fontSize: 13, lineHeight: 1.5,
              background: m.role === "user" ? C.blue : C.card,
              color: m.role === "user" ? "#fff" : C.textP,
              whiteSpace: "pre-wrap",
            }}>
              {m.text}
            </div>
            {m.role === "user" && (
              <div style={{ width: 24, height: 24, borderRadius: "50%", background: `${C.blue}18`, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0, marginTop: 2 }}>
                <User size={13} color={C.blue} />
              </div>
            )}
          </div>
        ))}
        {loading && (
          <div style={{ display: "flex", gap: 8, alignSelf: "flex-start" }}>
            <div style={{ width: 24, height: 24, borderRadius: "50%", background: `${C.blue}18`, display: "flex", alignItems: "center", justifyContent: "center", flexShrink: 0 }}>
              <Bot size={13} color={C.blue} />
            </div>
            <div style={{ padding: "8px 12px", borderRadius: 10, fontSize: 13, background: C.card, color: C.textM }}>Thinking...</div>
          </div>
        )}
        <div ref={endRef} />
      </div>
      <div style={{ padding: "10px 12px", borderTop: `0.5px solid ${C.border}`, display: "flex", gap: 8 }}>
        <input
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === "Enter" && handleSend()}
          placeholder="Ask about content, users, trends..."
          style={{ flex: 1, padding: "8px 12px", borderRadius: 8, border: `0.5px solid ${C.border}`, fontSize: 13, outline: "none", background: C.card }}
        />
        <button onClick={handleSend} disabled={loading} style={{ padding: "8px 12px", borderRadius: 8, background: C.blue, color: "#fff", border: "none", cursor: "pointer", opacity: loading ? 0.5 : 1 }}>
          <Send size={14} />
        </button>
      </div>
    </div>
  );
}

// --- Status badge ---
function StatusBadge({ status }) {
  const map = {
    keep: { bg: `${C.green}18`, color: C.green, icon: Check, label: "Keep" },
    review: { bg: `${C.yellow}18`, color: C.yellow, icon: Eye, label: "Review" },
    remove: { bg: `${C.red}18`, color: C.red, icon: Trash2, label: "Remove" },
  };
  const s = map[status] || map.review;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, fontWeight: 500, padding: "2px 8px", borderRadius: 4, background: s.bg, color: s.color }}>
      <s.icon size={11} />{s.label}
    </span>
  );
}

// --- Content table ---
function ContentTable({ data }) {
  const [sortKey, setSortKey] = useState("views");
  const [sortDir, setSortDir] = useState(-1);

  const sorted = [...data].sort((a, b) => (a[sortKey] > b[sortKey] ? 1 : -1) * sortDir);

  const toggleSort = (key) => {
    if (sortKey === key) setSortDir(d => d * -1);
    else { setSortKey(key); setSortDir(-1); }
  };

  const SortIcon = ({ k }) => sortKey === k ? (sortDir === -1 ? <ChevronDown size={11} /> : <ChevronUp size={11} />) : null;

  const th = { fontSize: 11, fontWeight: 500, color: C.textM, padding: "8px 10px", textAlign: "left", cursor: "pointer", userSelect: "none", borderBottom: `0.5px solid ${C.border}`, whiteSpace: "nowrap" };
  const td = { fontSize: 12, padding: "8px 10px", borderBottom: `0.5px solid ${C.border}`, color: C.textP };

  return (
    <div style={{ overflowX: "auto" }}>
      <table style={{ width: "100%", borderCollapse: "collapse" }}>
        <thead>
          <tr>
            <th style={th}>Content</th>
            <th style={th} onClick={() => toggleSort("views")}>Views <SortIcon k="views" /></th>
            <th style={th} onClick={() => toggleSort("trend")}>Trend <SortIcon k="trend" /></th>
            <th style={th}>Country</th>
            <th style={th}>Peak</th>
            <th style={th} onClick={() => toggleSort("retention")}>Retention <SortIcon k="retention" /></th>
            <th style={th}>Action</th>
          </tr>
        </thead>
        <tbody>
          {sorted.map(item => (
            <tr key={item.id} style={{ background: item.status === "remove" ? `${C.red}06` : "transparent" }}>
              <td style={td}>
                <div style={{ fontWeight: 500, fontSize: 12 }}>{item.title}</div>
                <div style={{ fontSize: 11, color: C.textM }}>{item.type}</div>
              </td>
              <td style={td}>{item.views.toLocaleString()}</td>
              <td style={{ ...td, color: item.trend >= 0 ? C.green : C.red, fontWeight: 500 }}>
                {item.trend >= 0 ? "+" : ""}{item.trend}%
              </td>
              <td style={td}>{item.country}</td>
              <td style={td}>{item.peakHour}</td>
              <td style={td}>
                <div style={{ display: "flex", alignItems: "center", gap: 4 }}>
                  <div style={{ width: 48, height: 4, borderRadius: 2, background: C.grid }}>
                    <div style={{ width: `${item.retention}%`, height: 4, borderRadius: 2, background: item.retention > 60 ? C.green : item.retention > 35 ? C.yellow : C.red }} />
                  </div>
                  <span style={{ fontSize: 11, color: C.textM }}>{item.retention}%</span>
                </div>
              </td>
              <td style={td}><StatusBadge status={item.status} /></td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// --- Main dashboard ---
export default function Dashboard() {
  const [usersData, setUsersData] = useState(() => genTimeSeries(60, 220, 60));
  const [sessionsData, setSessionsData] = useState(() => genTimeSeries(60, 480, 120));
  const [tab, setTab] = useState("content");

  useEffect(() => {
    const iv = setInterval(() => {
      setUsersData(prev => {
        const last = prev[prev.length - 1].value;
        const next = Math.max(0, Math.round(last + (Math.random() - 0.45) * 15));
        const t = new Date();
        return [...prev.slice(1), { time: `${String(t.getHours()).padStart(2,"0")}:${String(t.getMinutes()).padStart(2,"0")}`, value: next }];
      });
      setSessionsData(prev => {
        const last = prev[prev.length - 1].value;
        const next = Math.max(0, Math.round(last + (Math.random() - 0.45) * 30));
        const t = new Date();
        return [...prev.slice(1), { time: `${String(t.getHours()).padStart(2,"0")}:${String(t.getMinutes()).padStart(2,"0")}`, value: next }];
      });
    }, 3000);
    return () => clearInterval(iv);
  }, []);

  const latestUsers = usersData[usersData.length - 1]?.value || 0;
  const prevUsers = usersData[usersData.length - 2]?.value || latestUsers;
  const usersDelta = prevUsers ? Math.round(((latestUsers - prevUsers) / prevUsers) * 100) : 0;

  const latestSessions = sessionsData[sessionsData.length - 1]?.value || 0;
  const prevSessions = sessionsData[sessionsData.length - 2]?.value || latestSessions;
  const sessionsDelta = prevSessions ? Math.round(((latestSessions - prevSessions) / prevSessions) * 100) : 0;

  const sectionHead = { fontSize: 13, fontWeight: 500, color: C.textS, display: "flex", alignItems: "center", gap: 6, marginBottom: 12 };
  const card = { background: "#fff", border: `0.5px solid ${C.border}`, borderRadius: 12, padding: "14px 18px" };
  const tabBtn = (active) => ({
    fontSize: 12, fontWeight: 500, padding: "6px 14px", borderRadius: 6, cursor: "pointer", border: "none",
    background: active ? `${C.blue}14` : "transparent", color: active ? C.blue : C.textM,
  });

  return (
    <div style={{ display: "flex", height: "100vh", fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", color: C.textP, background: C.bg }}>
      {/* Left: Chat panel */}
      <div style={{ width: 340, minWidth: 300, borderRight: `0.5px solid ${C.border}`, flexShrink: 0 }}>
        <ChatPanel />
      </div>

      {/* Right: Dashboard */}
      <div style={{ flex: 1, overflowY: "auto", padding: "20px 24px" }}>
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20 }}>
          <div>
            <h1 style={{ fontSize: 18, fontWeight: 500, margin: 0 }}>Content analytics</h1>
            <p style={{ fontSize: 12, color: C.textM, margin: "2px 0 0" }}>Real-time monitoring and content intelligence</p>
          </div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ width: 6, height: 6, borderRadius: "50%", background: C.green, display: "inline-block" }} />
            <span style={{ fontSize: 11, color: C.textM }}>Live</span>
          </div>
        </div>

        {/* KPI row */}
        <div style={{ display: "flex", gap: 12, marginBottom: 24, flexWrap: "wrap" }}>
          <MetricTile label="Active users / min" value={latestUsers} delta={usersDelta} icon={Users} color={C.blue} data={usersData} />
          <MetricTile label="Active sessions / min" value={latestSessions} delta={sessionsDelta} icon={Activity} color={C.aqua} data={sessionsData} />
          <MetricTile label="Peak concurrent" value={Math.max(...sessionsData.map(d => d.value))} delta={8} icon={Zap} color={C.orange} data={null} />
          <MetricTile label="Avg watch time" value="14m 32s" delta={3} icon={Clock} color={C.magenta} data={null} />
        </div>

        {/* Zoomable time series */}
        <div style={sectionHead}>
          <BarChart3 size={14} />
          <span>Real-time trends</span>
          <span style={{ fontSize: 11, color: C.textM, fontWeight: 400, marginLeft: 4 }}>Drag the brush below to zoom and analyze</span>
        </div>
        <div style={{ ...card, marginBottom: 24 }}>
          <div style={{ display: "flex", gap: 16, marginBottom: 8, fontSize: 12, color: C.textS }}>
            <span style={{ display: "flex", alignItems: "center", gap: 4 }}><span style={{ width: 10, height: 10, borderRadius: 2, background: C.blue }} />Active users</span>
            <span style={{ display: "flex", alignItems: "center", gap: 4 }}><span style={{ width: 10, height: 10, borderRadius: 2, background: C.aqua }} />Sessions</span>
          </div>
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={usersData.map((u, i) => ({ ...u, sessions: sessionsData[i]?.value || 0 }))}>
              <CartesianGrid stroke={C.grid} strokeWidth={0.5} vertical={false} />
              <XAxis dataKey="time" tick={{ fontSize: 11, fill: C.textM }} axisLine={{ stroke: C.grid }} tickLine={false} />
              <YAxis tick={{ fontSize: 11, fill: C.textM }} axisLine={false} tickLine={false} width={40} />
              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: `0.5px solid ${C.border}`, boxShadow: "none" }} />
              <Area type="monotone" dataKey="value" name="Users" stroke={C.blue} fill={C.blue} fillOpacity={0.06} strokeWidth={2} dot={false} />
              <Area type="monotone" dataKey="sessions" name="Sessions" stroke={C.aqua} fill={C.aqua} fillOpacity={0.06} strokeWidth={2} dot={false} />
              <Brush dataKey="time" height={28} stroke={C.blue} fill={C.card} travellerWidth={8} startIndex={30} />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        {/* Peak hours chart */}
        <div style={sectionHead}>
          <Clock size={14} />
          <span>Traffic by hour of day</span>
        </div>
        <div style={{ ...card, marginBottom: 24 }}>
          <div style={{ display: "flex", gap: 16, marginBottom: 8, fontSize: 12, color: C.textS }}>
            <span style={{ display: "flex", alignItems: "center", gap: 4 }}><span style={{ width: 10, height: 10, borderRadius: 2, background: C.blue }} />Content</span>
            <span style={{ display: "flex", alignItems: "center", gap: 4 }}><span style={{ width: 10, height: 10, borderRadius: 2, background: C.orange }} />Scaling</span>
          </div>
          <ResponsiveContainer width="100%" height={200}>
            <BarChart data={PEAK_HOURS} barGap={2}>
              <CartesianGrid stroke={C.grid} strokeWidth={0.5} vertical={false} />
              <XAxis dataKey="hour" tick={{ fontSize: 11, fill: C.textM }} axisLine={{ stroke: C.grid }} tickLine={false} />
              <YAxis tick={{ fontSize: 11, fill: C.textM }} axisLine={false} tickLine={false} width={40} tickFormatter={v => v >= 1000 ? `${v / 1000}k` : v} />
              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: `0.5px solid ${C.border}`, boxShadow: "none" }} />
              <Bar dataKey="content" name="Content" fill={C.blue} radius={[4, 4, 0, 0]} maxBarSize={18} />
              <Bar dataKey="scaling" name="Scaling" fill={C.orange} radius={[4, 4, 0, 0]} maxBarSize={18} />
              <Brush dataKey="hour" height={24} stroke={C.orange} fill={C.card} travellerWidth={8} />
            </BarChart>
          </ResponsiveContainer>
        </div>

        {/* Content analytics tabs */}
        <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 16 }}>
          <button style={tabBtn(tab === "content")} onClick={() => setTab("content")}>
            <Film size={12} style={{ verticalAlign: -1, marginRight: 4 }} />Content ranking
          </button>
          <button style={tabBtn(tab === "geo")} onClick={() => setTab("geo")}>
            <Globe size={12} style={{ verticalAlign: -1, marginRight: 4 }} />By country
          </button>
          <button style={tabBtn(tab === "suggestions")} onClick={() => setTab("suggestions")}>
            <Sparkles size={12} style={{ verticalAlign: -1, marginRight: 4 }} />AI suggestions
          </button>
        </div>

        {tab === "content" && (
          <div style={{ ...card, padding: 0, overflow: "hidden", marginBottom: 24 }}>
            <ContentTable data={CONTENT_DATA} />
          </div>
        )}

        {tab === "geo" && (
          <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 24 }}>
            <div style={card}>
              <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 12 }}>Viewers by country</div>
              <ResponsiveContainer width="100%" height={240}>
                <PieChart>
                  <Pie data={COUNTRIES} dataKey="viewers" nameKey="name" cx="50%" cy="50%" outerRadius={90} innerRadius={50} paddingAngle={2} strokeWidth={0}>
                    {COUNTRIES.map((_, i) => <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
                  </Pie>
                  <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: `0.5px solid ${C.border}`, boxShadow: "none" }} formatter={(v) => v.toLocaleString()} />
                </PieChart>
              </ResponsiveContainer>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
                {COUNTRIES.map((c, i) => (
                  <span key={c.code} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: C.textS }}>
                    <span style={{ width: 8, height: 8, borderRadius: 2, background: PIE_COLORS[i % PIE_COLORS.length] }} />
                    {c.name} {c.pct}%
                  </span>
                ))}
              </div>
            </div>
            <div style={card}>
              <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 12 }}>Country breakdown</div>
              {COUNTRIES.map((c, i) => (
                <div key={c.code} style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 0", borderBottom: i < COUNTRIES.length - 1 ? `0.5px solid ${C.border}` : "none" }}>
                  <span style={{ fontSize: 12, fontWeight: 500, width: 28, color: C.textM }}>{c.code}</span>
                  <div style={{ flex: 1 }}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
                      <span style={{ fontSize: 12 }}>{c.name}</span>
                      <span style={{ fontSize: 11, color: C.textM }}>{c.viewers.toLocaleString()}</span>
                    </div>
                    <div style={{ height: 4, borderRadius: 2, background: C.grid }}>
                      <div style={{ width: `${c.pct}%`, height: 4, borderRadius: 2, background: PIE_COLORS[i % PIE_COLORS.length] }} />
                    </div>
                  </div>
                </div>
              ))}
            </div>
          </div>
        )}

        {tab === "suggestions" && (
          <div style={{ ...card, marginBottom: 24 }}>
            <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 16 }}>
              <Sparkles size={14} color={C.blue} />
              <span style={{ fontSize: 13, fontWeight: 500 }}>AI-powered content suggestions</span>
            </div>

            <div style={{ marginBottom: 20 }}>
              <div style={{ fontSize: 12, fontWeight: 500, color: C.green, marginBottom: 8, display: "flex", alignItems: "center", gap: 4 }}>
                <Check size={12} />Recommended to keep
              </div>
              {CONTENT_DATA.filter(c => c.status === "keep").map(c => (
                <div key={c.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 12px", borderRadius: 6, background: `${C.green}06`, marginBottom: 4 }}>
                  <div style={{ flex: 1 }}>
                    <span style={{ fontSize: 12, fontWeight: 500 }}>{c.title}</span>
                    <span style={{ fontSize: 11, color: C.textM, marginLeft: 8 }}>{c.type}</span>
                  </div>
                  <span style={{ fontSize: 11, color: C.green, fontWeight: 500 }}>+{c.trend}% trend</span>
                  <span style={{ fontSize: 11, color: C.textM }}>{c.retention}% retention</span>
                </div>
              ))}
            </div>

            <div style={{ marginBottom: 20 }}>
              <div style={{ fontSize: 12, fontWeight: 500, color: C.yellow, marginBottom: 8, display: "flex", alignItems: "center", gap: 4 }}>
                <Eye size={12} />Needs review
              </div>
              {CONTENT_DATA.filter(c => c.status === "review").map(c => (
                <div key={c.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 12px", borderRadius: 6, background: `${C.yellow}08`, marginBottom: 4 }}>
                  <div style={{ flex: 1 }}>
                    <span style={{ fontSize: 12, fontWeight: 500 }}>{c.title}</span>
                    <span style={{ fontSize: 11, color: C.textM, marginLeft: 8 }}>{c.type}</span>
                  </div>
                  <span style={{ fontSize: 11, color: C.textM }}>Low engagement — consider refreshing or promoting</span>
                </div>
              ))}
            </div>

            <div>
              <div style={{ fontSize: 12, fontWeight: 500, color: C.red, marginBottom: 8, display: "flex", alignItems: "center", gap: 4 }}>
                <Trash2 size={12} />Recommended to remove
              </div>
              {CONTENT_DATA.filter(c => c.status === "remove").map(c => (
                <div key={c.id} style={{ display: "flex", alignItems: "center", gap: 10, padding: "8px 12px", borderRadius: 6, background: `${C.red}06`, marginBottom: 4 }}>
                  <div style={{ flex: 1 }}>
                    <span style={{ fontSize: 12, fontWeight: 500 }}>{c.title}</span>
                    <span style={{ fontSize: 11, color: C.textM, marginLeft: 8 }}>{c.type}</span>
                  </div>
                  <span style={{ fontSize: 11, color: C.red, fontWeight: 500 }}>{c.trend}% trend</span>
                  <span style={{ fontSize: 11, color: C.textM }}>{c.retention}% retention</span>
                </div>
              ))}
            </div>

            <div style={{ marginTop: 16, padding: "10px 14px", borderRadius: 8, background: `${C.blue}08`, fontSize: 12, color: C.textS, lineHeight: 1.6 }}>
              <strong style={{ color: C.blue }}>Summary:</strong> 4 content items performing strongly with upward trends and high retention. 2 items need review — consider A/B testing new thumbnails or shifting time slots. 3 items recommended for removal due to declining engagement, low retention, or internal-only status.
            </div>
          </div>
        )}
      </div>
    </div>
  );
}