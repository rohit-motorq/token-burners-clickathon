import { useState, useEffect, useRef, useCallback } from "react";
import {
  AreaChart, Area, BarChart, Bar, LineChart, Line,
  XAxis, YAxis, CartesianGrid, Tooltip, ResponsiveContainer,
  Brush, PieChart, Pie, Cell, ReferenceLine
} from "recharts";
import {
  Users, Activity, Globe, Clock, Film, ChevronDown, ChevronUp,
  Zap, BarChart3, ArrowUpRight, ArrowDownRight,
  Play, Pause, RotateCcw, Filter, Info
} from "lucide-react";

// --- API helpers ---
async function apiGet(path, params = {}) {
  const qs = Object.entries(params)
    .filter(([, v]) => v !== undefined && v !== null && v !== "")
    .map(([k, v]) => `${encodeURIComponent(k)}=${encodeURIComponent(v)}`)
    .join("&");
  const res = await fetch(`/api/${path}${qs ? `?${qs}` : ""}`);
  if (!res.ok) throw new Error(`${path} → ${res.status}`);
  return res.json();
}

// ClickHouse DateTime "YYYY-MM-DD HH:MM:SS" (UTC) <-> JS Date
function chToDate(s) { return new Date(s.replace(" ", "T") + "Z"); }
function dateToCh(d) { return d.toISOString().slice(0, 19).replace("T", " "); }
function fmtClock(d) {
  return `${String(d.getUTCHours()).padStart(2, "0")}:${String(d.getUTCMinutes()).padStart(2, "0")}`;
}
function fmtDay(d) { return d.toISOString().slice(0, 10); }
// "YYYY-MM-DD HH:MM:SS" <-> <input type="datetime-local"> value ("YYYY-MM-DDTHH:MM")
function chToLocalInput(s) { return s ? s.replace(" ", "T").slice(0, 16) : ""; }
function localInputToCh(s) { return s ? `${s.replace("T", " ")}:00` : ""; }

// --- Color tokens ---
const C = {
  blue: "#2a78d6", orange: "#eb6834", aqua: "#1baf7a",
  yellow: "#eda100", magenta: "#e87ba4", green: "#0ca30c",
  red: "#d03b3b", violet: "#4a3aa7",
  bg: "#fcfcfb", card: "#f7f7f6", border: "rgba(11,11,11,0.10)",
  textP: "#0b0b0b", textS: "#52514e", textM: "#898781",
  grid: "#e1e0d9"
};

const PIE_COLORS = [C.blue, C.aqua, C.orange, C.yellow, C.magenta, C.green, C.violet, C.red];

// --- Info hint: small (i) icon, native title tooltip on hover — explains
// what a chart/metric actually shows, no extra deps needed for that.
function InfoHint({ text }) {
  return (
    <span title={text} style={{ display: "inline-flex", cursor: "help", color: C.textM }}>
      <Info size={12} />
    </span>
  );
}

// --- Metric tile ---
function MetricTile({ label, value, delta, icon: Icon, color, data, hint }) {
  const isUp = delta >= 0;
  return (
    <div style={{ background: C.card, borderRadius: 8, padding: "14px 16px", flex: 1, minWidth: 170 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, marginBottom: 6 }}>
        <Icon size={14} color={C.textM} />
        <span style={{ fontSize: 12, color: C.textM }}>{label}</span>
        {hint && <InfoHint text={hint} />}
      </div>
      <div style={{ display: "flex", alignItems: "baseline", gap: 8 }}>
        <span style={{ fontSize: 32, fontWeight: 500, color: C.textP, fontFamily: "Georgia, serif", letterSpacing: "-0.02em" }}>
          {typeof value === "number" ? value.toLocaleString() : value}
        </span>
        {delta !== null && delta !== undefined && (
          <span style={{ fontSize: 12, fontWeight: 500, color: isUp ? C.green : C.red, display: "flex", alignItems: "center", gap: 2 }}>
            {isUp ? <ArrowUpRight size={12} /> : <ArrowDownRight size={12} />}
            {Math.abs(delta)}%
          </span>
        )}
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

// --- Replay panel: pick a stored time range, scrub/play back the real curve ---
function ReplayPanel({ meta, card, sectionHead }) {
  const bounds = meta && meta.min_ts && meta.max_ts
    ? { min: chToDate(meta.min_ts), max: chToDate(meta.max_ts) }
    : null;
  const totalMin = bounds ? Math.max(1, Math.round((bounds.max - bounds.min) / 60000)) : 1;

  // slider positions are minute-offsets from bounds.min
  const [startOff, setStartOff] = useState(0);
  const [endOff, setEndOff] = useState(totalMin);
  useEffect(() => { setEndOff(totalMin); }, [totalMin]);

  const [curve, setCurve] = useState([]);
  const [playedCount, setPlayedCount] = useState(0);
  const [playing, setPlaying] = useState(false);
  const [speed, setSpeed] = useState(4);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);
  const idxRef = useRef(0);
  const timerRef = useRef(null);
  const tickMs = Math.max(20, 300 / speed);

  const rangeStart = bounds ? new Date(bounds.min.getTime() + startOff * 60000) : null;
  const rangeEnd = bounds ? new Date(bounds.min.getTime() + endOff * 60000) : null;
  const spanMin = startOff < endOff ? endOff - startOff : 0;
  const grain = spanMin > 60 * 24 * 10 ? "day" : spanMin > 60 * 24 ? "hour" : "minute";

  const stopTimer = () => { if (timerRef.current) { clearInterval(timerRef.current); timerRef.current = null; } };

  const loadReplay = useCallback(async () => {
    if (!bounds || spanMin <= 0) return;
    stopTimer();
    setPlaying(false);
    setLoading(true);
    setError(null);
    try {
      const rows = await apiGet("concurrency", {
        start: dateToCh(rangeStart), end: dateToCh(rangeEnd), grain,
      });
      setCurve(rows);
      setPlayedCount(0);
      idxRef.current = 0;
    } catch (e) {
      setError(String(e.message || e));
      setCurve([]);
    }
    setLoading(false);
  }, [bounds, spanMin, rangeStart?.getTime(), rangeEnd?.getTime(), grain]);

  useEffect(() => {
    if (!playing || curve.length === 0) return;
    timerRef.current = setInterval(() => {
      idxRef.current += 1;
      if (idxRef.current > curve.length) {
        stopTimer();
        setPlaying(false);
        return;
      }
      setPlayedCount(idxRef.current);
    }, tickMs);
    return stopTimer;
  }, [playing, curve, tickMs]);

  const togglePlay = () => {
    if (curve.length === 0) return;
    if (idxRef.current >= curve.length) { idxRef.current = 0; setPlayedCount(0); }
    setPlaying(p => !p);
  };
  const reset = () => { stopTimer(); setPlaying(false); idxRef.current = 0; setPlayedCount(0); };

  // Data array length never changes during playback — only how far the line
  // is revealed (nulls past the playhead, connectNulls={false}). That keeps
  // the X axis fixed across the whole run so the chart reads as the line
  // actually growing/animating in, instead of the axis re-laying-out and
  // the whole shape jumping on every tick.
  const revealed = playing || playedCount > 0;
  const chartData = revealed
    ? curve.map((r, i) => ({ ...r, concurrency: i < playedCount ? r.concurrency : null }))
    : curve;
  const playedSlice = revealed ? curve.slice(0, playedCount) : curve;
  const peak = playedSlice.length ? Math.max(...playedSlice.map(r => r.concurrency)) : 0;
  const playheadBucket = revealed && playedCount > 0 ? curve[playedCount - 1]?.bucket : null;

  return (
    <>
      <div style={sectionHead}>
        <Play size={14} />
        <span>Replay stored data</span>
        <InfoHint text="Fetches the real concurrency curve for the picked range once, then reveals it point by point client-side — not a live stream. Grain (minute/hour/day) is picked automatically based on how wide the range is." />
        <span style={{ fontSize: 11, color: C.textM, fontWeight: 400, marginLeft: 4 }}>
          Pick a range from all stored history, then play it back
        </span>
      </div>
      <div style={{ ...card, marginBottom: 24 }}>
        {!bounds ? (
          <div style={{ fontSize: 12, color: C.textM }}>Loading available time range…</div>
        ) : (
          <>
            <div style={{ display: "flex", justifyContent: "space-between", fontSize: 11, color: C.textM, marginBottom: 4 }}>
              <span>{fmtDay(bounds.min)} {fmtClock(bounds.min)}</span>
              <span>{fmtDay(bounds.max)} {fmtClock(bounds.max)}</span>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 6, marginBottom: 10 }}>
              <label style={{ fontSize: 11, color: C.textS, display: "flex", alignItems: "center", gap: 8 }}>
                Start <span style={{ color: C.textP, fontWeight: 500 }}>{fmtDay(rangeStart)} {fmtClock(rangeStart)}</span>
                <input type="range" min={0} max={totalMin} value={startOff}
                  onChange={e => setStartOff(Math.min(Number(e.target.value), endOff - 1))}
                  style={{ flex: 1, accentColor: C.blue }} />
              </label>
              <label style={{ fontSize: 11, color: C.textS, display: "flex", alignItems: "center", gap: 8 }}>
                End <span style={{ color: C.textP, fontWeight: 500 }}>{fmtDay(rangeEnd)} {fmtClock(rangeEnd)}</span>
                <input type="range" min={0} max={totalMin} value={endOff}
                  onChange={e => setEndOff(Math.max(Number(e.target.value), startOff + 1))}
                  style={{ flex: 1, accentColor: C.orange }} />
              </label>
            </div>

            <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
              <button onClick={loadReplay} disabled={loading || spanMin <= 0}
                style={{ fontSize: 12, fontWeight: 500, padding: "6px 14px", borderRadius: 6, border: "none", cursor: "pointer", background: C.blue, color: "#fff", opacity: loading ? 0.6 : 1 }}>
                {loading ? "Loading…" : "Replay"}
              </button>
              <button onClick={togglePlay} disabled={curve.length === 0}
                style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12, padding: "6px 12px", borderRadius: 6, border: `0.5px solid ${C.border}`, cursor: "pointer", background: "#fff", opacity: curve.length ? 1 : 0.5 }}>
                {playing ? <Pause size={12} /> : <Play size={12} />}{playing ? "Pause" : "Play"}
              </button>
              <button onClick={reset} disabled={curve.length === 0}
                style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 12, padding: "6px 12px", borderRadius: 6, border: `0.5px solid ${C.border}`, cursor: "pointer", background: "#fff", opacity: curve.length ? 1 : 0.5 }}>
                <RotateCcw size={12} />Reset
              </button>
              <select value={speed} onChange={e => setSpeed(Number(e.target.value))}
                style={{ fontSize: 12, padding: "6px 8px", borderRadius: 6, border: `0.5px solid ${C.border}`, background: "#fff" }}>
                <option value={1}>1x</option>
                <option value={4}>4x</option>
                <option value={16}>16x</option>
                <option value={64}>64x</option>
              </select>
              <span style={{ fontSize: 11, color: C.textM }}>
                {spanMin > 0 ? `${spanMin.toLocaleString()} min window · ${grain} grain` : "select a valid range"}
              </span>
              {curve.length > 0 && (
                <span style={{ fontSize: 11, color: C.textM, marginLeft: "auto" }}>
                  Peak in range: <strong style={{ color: C.textP }}>{peak.toLocaleString()}</strong>
                  {" · "}{playedCount}/{curve.length} points played
                </span>
              )}
            </div>

            {error && <div style={{ fontSize: 12, color: C.red, marginBottom: 8 }}>{error}</div>}

            <ResponsiveContainer width="100%" height={220}>
              <AreaChart data={chartData}>
                <CartesianGrid stroke={C.grid} strokeWidth={0.5} vertical={false} />
                <XAxis dataKey="bucket" tick={{ fontSize: 10, fill: C.textM }} axisLine={{ stroke: C.grid }} tickLine={false}
                  tickFormatter={v => typeof v === "string" ? v.slice(5, 16) : v} />
                <YAxis tick={{ fontSize: 11, fill: C.textM }} axisLine={false} tickLine={false} width={40} />
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: `0.5px solid ${C.border}`, boxShadow: "none" }} />
                {playheadBucket !== null && (
                  <ReferenceLine x={playheadBucket} stroke={C.orange} strokeDasharray="3 3" strokeWidth={1.5} />
                )}
                <Area type="monotone" dataKey="concurrency" name="Concurrent sessions" stroke={C.blue} fill={C.blue}
                  fillOpacity={0.1} strokeWidth={2} dot={false} connectNulls={false}
                  isAnimationActive={playing} animationDuration={tickMs} animationEasing="linear" />
              </AreaChart>
            </ResponsiveContainer>
          </>
        )}
      </div>
    </>
  );
}

const TRAFFIC_GRAINS = [
  { key: "minute", label: "Minute of day" },
  { key: "hour", label: "Hour of day" },
  { key: "day", label: "Day" },
];
const TRAFFIC_SPLITS = [
  { key: "", label: "No breakdown" },
  { key: "country", label: "Country" },
  { key: "platform", label: "Platform" },
  { key: "video_resolution", label: "Resolution" },
  { key: "video_type", label: "Video type" },
  { key: "category", label: "Category" },
  { key: "show_name", label: "Show name" },
  { key: "title", label: "Title" },
];

function minuteOfDayLabel(v) {
  const h = Math.floor(v / 60), m = v % 60;
  return `${String(h).padStart(2, "0")}:${String(m).padStart(2, "0")}`;
}

// --- Traffic chart: time axis (minute/hour/day) x optional dimension split ---
function TrafficByDimension({ meta, card, sectionHead }) {
  const [grain, setGrain] = useState("hour");
  const [splitBy, setSplitBy] = useState("");
  const [filters, setFilters] = useState({ platform: "", country: "", video_type: "", category: "", show_name: "", title: "" });
  const [result, setResult] = useState({ series: ["value"], rows: [] });
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  const bounds = meta && meta.min_ts && meta.max_ts
    ? { start: meta.min_ts, end: meta.max_ts }
    : null;

  const load = useCallback(async () => {
    if (!bounds) return;
    setLoading(true);
    setError(null);
    try {
      const res = await apiGet("traffic", { ...bounds, grain, split_by: splitBy || undefined, ...filters });
      setResult(res);
    } catch (e) {
      setError(String(e.message || e));
      setResult({ series: ["value"], rows: [] });
    }
    setLoading(false);
  }, [bounds?.start, bounds?.end, grain, splitBy, filters.platform, filters.country, filters.video_type, filters.category, filters.show_name, filters.title]);

  useEffect(() => { load(); }, [load]);

  const dimBtn = (active) => ({
    fontSize: 11, fontWeight: 500, padding: "5px 10px", borderRadius: 6, cursor: "pointer",
    border: `0.5px solid ${active ? C.blue : C.border}`, background: active ? `${C.blue}14` : "#fff",
    color: active ? C.blue : C.textS,
  });
  const selectStyle = { fontSize: 11, padding: "5px 8px", borderRadius: 6, border: `0.5px solid ${C.border}`, background: "#fff", color: C.textS };

  const data = result.rows.map(r => ({
    ...r,
    bucket: grain === "minute" ? minuteOfDayLabel(r.bucket) : String(r.bucket),
  }));
  const series = result.series || ["value"];
  // 1440 minute-of-day points read far better as a line than as bars —
  // bars only work once buckets are sparse (hour: 24, day: window length).
  const ChartTag = grain === "minute" ? LineChart : BarChart;
  const SeriesTag = grain === "minute" ? Line : Bar;

  return (
    <>
      <div style={sectionHead}>
        <Clock size={14} />
        <span>Traffic</span>
        <InfoHint text="Session volume (count of session starts), not concurrency — a busy hour here doesn't mean high concurrent viewership, just many sessions began. Minute/hour-of-day buckets are a repeating pattern averaged across every day in range; Day is real calendar dates. Dimension breakdowns cap at the top 8 values by volume, the rest collapse into 'Other'. Title filters/splits match the exact title string (no partial match)." />
        <span style={{ fontSize: 11, color: C.textM, fontWeight: 400, marginLeft: 4 }}>
          Across all stored history · combine a time axis with a dimension breakdown
        </span>
      </div>
      <div style={{ ...card, marginBottom: 24 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 8, marginBottom: 12, flexWrap: "wrap" }}>
          <div style={{ display: "flex", gap: 4 }}>
            {TRAFFIC_GRAINS.map(g => (
              <button key={g.key} style={dimBtn(grain === g.key)} onClick={() => setGrain(g.key)}>{g.label}</button>
            ))}
          </div>
          <span style={{ color: C.textM, fontSize: 11 }}>×</span>
          <select style={selectStyle} value={splitBy} onChange={e => setSplitBy(e.target.value)}>
            {TRAFFIC_SPLITS.map(s => <option key={s.key} value={s.key}>{s.label}</option>)}
          </select>
          <div style={{ display: "flex", alignItems: "center", gap: 6, marginLeft: "auto", flexWrap: "wrap" }}>
            <Filter size={11} color={C.textM} />
            <select style={selectStyle} value={filters.platform} onChange={e => setFilters(f => ({ ...f, platform: e.target.value }))}>
              <option value="">All platforms</option>
              {(meta?.platforms || []).map(p => <option key={p} value={p}>{p}</option>)}
            </select>
            <select style={selectStyle} value={filters.country} onChange={e => setFilters(f => ({ ...f, country: e.target.value }))}>
              <option value="">All countries</option>
              {(meta?.countries || []).map(c => <option key={c} value={c}>{c}</option>)}
            </select>
            <select style={selectStyle} value={filters.video_type} onChange={e => setFilters(f => ({ ...f, video_type: e.target.value }))}>
              <option value="">All video types</option>
              {(meta?.video_types || []).map(v => <option key={v} value={v}>{v}</option>)}
            </select>
            <select style={selectStyle} value={filters.category} onChange={e => setFilters(f => ({ ...f, category: e.target.value }))}>
              <option value="">All categories</option>
              {(meta?.categories || []).map(c => <option key={c} value={c}>{c}</option>)}
            </select>
            <select style={selectStyle} value={filters.show_name} onChange={e => setFilters(f => ({ ...f, show_name: e.target.value }))}>
              <option value="">All shows</option>
              {(meta?.show_names || []).map(s => <option key={s} value={s}>{s}</option>)}
            </select>
            <input style={{ ...selectStyle, width: 120 }} placeholder="Exact title…" value={filters.title}
              onChange={e => setFilters(f => ({ ...f, title: e.target.value }))} />
          </div>
        </div>

        {error && <div style={{ fontSize: 12, color: C.red, marginBottom: 8 }}>{error}</div>}
        {loading && <div style={{ fontSize: 12, color: C.textM, marginBottom: 8 }}>Loading…</div>}

        {series.length > 1 && (
          <div style={{ display: "flex", gap: 12, marginBottom: 8, fontSize: 11, color: C.textS, flexWrap: "wrap" }}>
            {series.map((s, i) => (
              <span key={s} style={{ display: "flex", alignItems: "center", gap: 4 }}>
                <span style={{ width: 8, height: 8, borderRadius: 2, background: PIE_COLORS[i % PIE_COLORS.length] }} />{s}
              </span>
            ))}
          </div>
        )}

        <ResponsiveContainer width="100%" height={220}>
          <ChartTag data={data} barGap={2}>
            <CartesianGrid stroke={C.grid} strokeWidth={0.5} vertical={false} />
            <XAxis dataKey="bucket" tick={{ fontSize: 10, fill: C.textM }} axisLine={{ stroke: C.grid }} tickLine={false}
              interval={grain === "minute" ? 59 : "preserveStartEnd"} />
            <YAxis tick={{ fontSize: 11, fill: C.textM }} axisLine={false} tickLine={false} width={40} tickFormatter={v => v >= 1000 ? `${v / 1000}k` : v} />
            <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: `0.5px solid ${C.border}`, boxShadow: "none" }} />
            {series.map((s, i) => (
              <SeriesTag key={s} dataKey={s} name={s} type="monotone"
                stroke={PIE_COLORS[i % PIE_COLORS.length]} fill={PIE_COLORS[i % PIE_COLORS.length]}
                fillOpacity={grain === "minute" ? undefined : 1} strokeWidth={2} dot={false}
                radius={grain === "minute" ? undefined : [4, 4, 0, 0]} maxBarSize={24} isAnimationActive={false} />
            ))}
          </ChartTag>
        </ResponsiveContainer>
      </div>
    </>
  );
}

// --- Content ranking table: fetches /api/content for the given window ---
function ContentTable({ start, end, card, sectionHead }) {
  const [sortKey, setSortKey] = useState("sessions");
  const [sortDir, setSortDir] = useState(-1);
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!start || !end) return;
    setLoading(true);
    setError(null);
    apiGet("content", { start, end, limit: 20 })
      .then(setRows)
      .catch(e => { setError(String(e.message || e)); setRows([]); })
      .finally(() => setLoading(false));
  }, [start, end]);

  const sorted = [...rows].sort((a, b) => (a[sortKey] > b[sortKey] ? 1 : -1) * sortDir);

  const toggleSort = (key) => {
    if (sortKey === key) setSortDir(d => d * -1);
    else { setSortKey(key); setSortDir(-1); }
  };

  const SortIcon = ({ k }) => sortKey === k ? (sortDir === -1 ? <ChevronDown size={11} /> : <ChevronUp size={11} />) : null;

  const th = { fontSize: 11, fontWeight: 500, color: C.textM, padding: "8px 10px", textAlign: "left", cursor: "pointer", userSelect: "none", borderBottom: `0.5px solid ${C.border}`, whiteSpace: "nowrap" };
  const td = { fontSize: 12, padding: "8px 10px", borderBottom: `0.5px solid ${C.border}`, color: C.textP };

  return (
    <>
      <div style={sectionHead}>
        <Film size={14} />
        <span>Content ranking</span>
        <span style={{ fontSize: 11, color: C.textM, fontWeight: 400, marginLeft: 4 }}>By session volume, selected window</span>
      </div>
      <div style={{ ...card, padding: 0, overflow: "hidden", marginBottom: 24 }}>
        {error && <div style={{ fontSize: 12, color: C.red, padding: 12 }}>{error}</div>}
        {loading && <div style={{ fontSize: 12, color: C.textM, padding: 12 }}>Loading…</div>}
        {!loading && !error && rows.length === 0 && <div style={{ fontSize: 12, color: C.textM, padding: 12 }}>No data for this window.</div>}
        {rows.length > 0 && (
          <div style={{ overflowX: "auto" }}>
            <table style={{ width: "100%", borderCollapse: "collapse" }}>
              <thead>
                <tr>
                  <th style={th}>Content</th>
                  <th style={th} onClick={() => toggleSort("sessions")}>Sessions <SortIcon k="sessions" /></th>
                  <th style={th}>Video type</th>
                  <th style={th}>Category</th>
                </tr>
              </thead>
              <tbody>
                {sorted.map(item => (
                  <tr key={item.content_id}>
                    <td style={td}>
                      <div style={{ fontWeight: 500, fontSize: 12 }}>{item.title}</div>
                      <div style={{ fontSize: 11, color: C.textM }}>#{item.content_id}</div>
                    </td>
                    <td style={td}>{item.sessions.toLocaleString()}</td>
                    <td style={td}>{item.video_type}</td>
                    <td style={td}>{item.category}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        )}
      </div>
    </>
  );
}

// --- Geo breakdown: fetches /api/geo for the given window ---
function GeoBreakdown({ start, end, card }) {
  const [rows, setRows] = useState([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(null);

  useEffect(() => {
    if (!start || !end) return;
    setLoading(true);
    setError(null);
    apiGet("geo", { start, end })
      .then(setRows)
      .catch(e => { setError(String(e.message || e)); setRows([]); })
      .finally(() => setLoading(false));
  }, [start, end]);

  const total = rows.reduce((s, r) => s + r.sessions, 0) || 1;

  return (
    <div style={{ display: "grid", gridTemplateColumns: "1fr 1fr", gap: 16, marginBottom: 24 }}>
      <div style={card}>
        <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 12 }}>Sessions by country</div>
        {error && <div style={{ fontSize: 12, color: C.red }}>{error}</div>}
        {loading && <div style={{ fontSize: 12, color: C.textM }}>Loading…</div>}
        {rows.length > 0 && (
          <>
            <ResponsiveContainer width="100%" height={240}>
              <PieChart>
                <Pie data={rows} dataKey="sessions" nameKey="country" cx="50%" cy="50%" outerRadius={90} innerRadius={50} paddingAngle={2} strokeWidth={0}>
                  {rows.map((_, i) => <Cell key={i} fill={PIE_COLORS[i % PIE_COLORS.length]} />)}
                </Pie>
                <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: `0.5px solid ${C.border}`, boxShadow: "none" }} formatter={(v) => v.toLocaleString()} />
              </PieChart>
            </ResponsiveContainer>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 8, marginTop: 8 }}>
              {rows.map((c, i) => (
                <span key={c.country} style={{ display: "flex", alignItems: "center", gap: 4, fontSize: 11, color: C.textS }}>
                  <span style={{ width: 8, height: 8, borderRadius: 2, background: PIE_COLORS[i % PIE_COLORS.length] }} />
                  {c.country} {Math.round((c.sessions / total) * 100)}%
                </span>
              ))}
            </div>
          </>
        )}
      </div>
      <div style={card}>
        <div style={{ fontSize: 13, fontWeight: 500, marginBottom: 12 }}>Country breakdown</div>
        {rows.map((c, i) => (
          <div key={c.country} style={{ display: "flex", alignItems: "center", gap: 10, padding: "6px 0", borderBottom: i < rows.length - 1 ? `0.5px solid ${C.border}` : "none" }}>
            <span style={{ fontSize: 12, fontWeight: 500, width: 40, color: C.textM }}>{c.country}</span>
            <div style={{ flex: 1 }}>
              <div style={{ display: "flex", justifyContent: "space-between", marginBottom: 2 }}>
                <span style={{ fontSize: 12 }}>{c.sessions.toLocaleString()} sessions</span>
                <span style={{ fontSize: 11, color: C.textM }}>{Math.round(c.users).toLocaleString()} users</span>
              </div>
              <div style={{ height: 4, borderRadius: 2, background: C.grid }}>
                <div style={{ width: `${Math.round((c.sessions / total) * 100)}%`, height: 4, borderRadius: 2, background: PIE_COLORS[i % PIE_COLORS.length] }} />
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}

// --- Analysis window picker: presets anchored to the dataset's latest
// timestamp (this is a frozen historical dataset, not a live feed, so
// "today"/"past week" mean relative to the newest stored data, not
// wall-clock now) plus a custom range toggle ---
const WINDOW_PRESETS = [
  { key: "day", label: "Daily", days: 1 },
  { key: "week", label: "Past week", days: 7 },
  { key: "month", label: "Past month", days: 30 },
];

function AnalysisWindowPicker({ meta, range, setRange, preset, setPreset }) {
  if (!meta?.min_ts || !meta?.max_ts) return null;
  const minTs = chToDate(meta.min_ts), maxTs = chToDate(meta.max_ts);

  const applyPreset = (p) => {
    setPreset(p.key);
    const start = new Date(Math.max(minTs.getTime(), maxTs.getTime() - p.days * 86400000));
    setRange({ start: dateToCh(start), end: dateToCh(maxTs) });
  };

  const btnStyle = (active) => ({
    fontSize: 11, fontWeight: 500, padding: "5px 10px", borderRadius: 6, cursor: "pointer",
    border: `0.5px solid ${active ? C.blue : "rgba(11,11,11,0.10)"}`, background: active ? "#2a78d614" : "#fff",
    color: active ? C.blue : "#52514e",
  });

  return (
    <div style={{ display: "flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
      {WINDOW_PRESETS.map(p => (
        <button key={p.key} style={btnStyle(preset === p.key)} onClick={() => applyPreset(p)}>{p.label}</button>
      ))}
      <button style={btnStyle(preset === "custom")} onClick={() => setPreset("custom")}>Custom</button>
      {preset === "custom" && range && (
        <>
          <input type="datetime-local" value={chToLocalInput(range.start)}
            onChange={e => setRange(r => ({ ...r, start: localInputToCh(e.target.value) }))}
            style={{ fontSize: 11, padding: "5px 6px", borderRadius: 6, border: "0.5px solid rgba(11,11,11,0.10)" }} />
          <span style={{ color: "#898781", fontSize: 11 }}>to</span>
          <input type="datetime-local" value={chToLocalInput(range.end)}
            onChange={e => setRange(r => ({ ...r, end: localInputToCh(e.target.value) }))}
            style={{ fontSize: 11, padding: "5px 6px", borderRadius: 6, border: "0.5px solid rgba(11,11,11,0.10)" }} />
        </>
      )}
    </div>
  );
}

// --- Main dashboard ---
export default function Dashboard() {
  const [tab, setTab] = useState("content");
  const [meta, setMeta] = useState(null);
  const [range, setRange] = useState(null); // { start, end } in CH DateTime strings — analysis window shared by KPIs/trend/content/geo
  const [preset, setPreset] = useState("week");

  useEffect(() => {
    apiGet("meta").then(m => {
      setMeta(m);
      if (m?.min_ts && m?.max_ts) {
        const maxTs = chToDate(m.max_ts);
        const start = new Date(Math.max(chToDate(m.min_ts).getTime(), maxTs.getTime() - 7 * 86400000));
        setRange({ start: dateToCh(start), end: dateToCh(maxTs) });
      }
    }).catch(() => setMeta(null));
  }, []);

  const [curve, setCurve] = useState([]);
  const [kpis, setKpis] = useState(null);
  const [loadingCurve, setLoadingCurve] = useState(false);
  const [curveError, setCurveError] = useState(null);

  useEffect(() => {
    if (!range) return;
    setLoadingCurve(true);
    setCurveError(null);
    const spanMin = (chToDate(range.end) - chToDate(range.start)) / 60000;
    const grain = spanMin > 60 * 24 * 10 ? "day" : spanMin > 60 * 24 ? "hour" : "minute";
    Promise.all([
      apiGet("concurrency", { start: range.start, end: range.end, grain }),
      apiGet("kpis", { start: range.start, end: range.end }),
    ]).then(([curveRows, kpiRow]) => {
      setCurve(curveRows);
      setKpis(kpiRow);
    }).catch(e => {
      setCurveError(String(e.message || e));
      setCurve([]);
      setKpis(null);
    }).finally(() => setLoadingCurve(false));
  }, [range?.start, range?.end]);

  const sectionHead = { fontSize: 13, fontWeight: 500, color: C.textS, display: "flex", alignItems: "center", gap: 6, marginBottom: 12 };
  const card = { background: "#fff", border: `0.5px solid ${C.border}`, borderRadius: 12, padding: "14px 18px" };
  const tabBtn = (active) => ({
    fontSize: 12, fontWeight: 500, padding: "6px 14px", borderRadius: 6, cursor: "pointer", border: "none",
    background: active ? `${C.blue}14` : "transparent", color: active ? C.blue : C.textM,
  });

  const fmtSecs = (s) => {
    if (!s) return "—";
    const m = Math.floor(s / 60), r = Math.round(s % 60);
    return `${m}m ${r}s`;
  };

  return (
    <div style={{ minHeight: "100vh", fontFamily: "-apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif", color: C.textP, background: C.bg }}>
      <div style={{ maxWidth: 1280, margin: "0 auto", padding: "20px 24px" }}>
        {/* Header */}
        <div style={{ display: "flex", alignItems: "center", justifyContent: "space-between", marginBottom: 20, flexWrap: "wrap", gap: 12 }}>
          <div>
            <h1 style={{ fontSize: 18, fontWeight: 500, margin: 0 }}>Content analytics</h1>
            <p style={{ fontSize: 12, color: C.textM, margin: "2px 0 0" }}>Concurrency, traffic and content intelligence over stored history</p>
          </div>
          <AnalysisWindowPicker meta={meta} range={range} setRange={setRange} preset={preset} setPreset={setPreset} />
        </div>

        {/* KPI row */}
        <div style={{ display: "flex", gap: 12, marginBottom: 24, flexWrap: "wrap" }}>
          <MetricTile label="Current concurrency" value={kpis?.current_concurrency ?? "—"} delta={null} icon={Activity} color={C.aqua} data={curve.map(r => ({ value: r.concurrency }))}
            hint="Concurrent sessions in the last minute of the analysis window — a running sum of session start/end deltas, not a live poll." />
          <MetricTile label="Peak concurrency" value={kpis?.peak_concurrency ?? "—"} delta={null} icon={Zap} color={C.orange} data={null}
            hint="The single highest concurrency value reached anywhere inside the analysis window." />
          <MetricTile label="Avg concurrency" value={kpis?.avg_concurrency ?? "—"} delta={null} icon={BarChart3} color={C.blue} data={null}
            hint="Mean concurrency across every minute in the window, including minutes with zero activity." />
          <MetricTile label="Distinct active users" value={kpis?.distinct_active_users ?? "—"} delta={null} icon={Users} color={C.magenta} data={null}
            hint="Unique users whose session started inside this window — a user active only via a session that started earlier is not counted." />
          <MetricTile label="Avg watch time" value={fmtSecs(kpis?.avg_watch_seconds)} delta={null} icon={Clock} color={C.green} data={null}
            hint="Average (last event time − first event time) per session, for sessions that started inside this window." />
        </div>

        {/* Concurrency over the analysis window */}
        <div style={sectionHead}>
          <BarChart3 size={14} />
          <span>Concurrent sessions</span>
          <InfoHint text="How many sessions were simultaneously active at each point in time — a running sum of +1/-1 session start/end events, not a raw event count. Zero-filled at the selected grain, so gaps mean genuinely zero concurrency, not missing data." />
          <span style={{ fontSize: 11, color: C.textM, fontWeight: 400, marginLeft: 4 }}>Drag the brush below to zoom into the analysis window</span>
        </div>
        <div style={{ ...card, marginBottom: 24 }}>
          {curveError && <div style={{ fontSize: 12, color: C.red, marginBottom: 8 }}>{curveError}</div>}
          {loadingCurve && <div style={{ fontSize: 12, color: C.textM, marginBottom: 8 }}>Loading…</div>}
          <ResponsiveContainer width="100%" height={240}>
            <AreaChart data={curve}>
              <CartesianGrid stroke={C.grid} strokeWidth={0.5} vertical={false} />
              <XAxis dataKey="bucket" tick={{ fontSize: 10, fill: C.textM }} axisLine={{ stroke: C.grid }} tickLine={false}
                tickFormatter={v => typeof v === "string" ? v.slice(5, 16) : v} />
              <YAxis tick={{ fontSize: 11, fill: C.textM }} axisLine={false} tickLine={false} width={40} />
              <Tooltip contentStyle={{ fontSize: 12, borderRadius: 8, border: `0.5px solid ${C.border}`, boxShadow: "none" }} />
              <Area type="monotone" dataKey="concurrency" name="Concurrent sessions" stroke={C.blue} fill={C.blue} fillOpacity={0.06} strokeWidth={2} dot={false} isAnimationActive={false} />
              <Brush dataKey="bucket" height={28} stroke={C.blue} fill={C.card} travellerWidth={8} tickFormatter={v => typeof v === "string" ? v.slice(5, 16) : v} />
            </AreaChart>
          </ResponsiveContainer>
        </div>

        <TrafficByDimension meta={meta} card={card} sectionHead={sectionHead} />

        <ReplayPanel meta={meta} card={card} sectionHead={sectionHead} />

        {/* Content analytics tabs */}
        <div style={{ display: "flex", alignItems: "center", gap: 4, marginBottom: 16 }}>
          <button style={tabBtn(tab === "content")} onClick={() => setTab("content")}>
            <Film size={12} style={{ verticalAlign: -1, marginRight: 4 }} />Content ranking
          </button>
          <button style={tabBtn(tab === "geo")} onClick={() => setTab("geo")}>
            <Globe size={12} style={{ verticalAlign: -1, marginRight: 4 }} />By country
          </button>
        </div>

        {tab === "content" && range && <ContentTable start={range.start} end={range.end} card={card} sectionHead={sectionHead} />}
        {tab === "geo" && range && <GeoBreakdown start={range.start} end={range.end} card={card} />}
      </div>
    </div>
  );
}