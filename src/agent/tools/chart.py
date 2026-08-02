"""Renders a time series server-side. LLM only decides when to call this and
captions the result — it never generates pixels or a chart spec itself.

Styling follows the dataviz skill's method (references/palette.md,
marks-and-anatomy.md) scoped to what applies to a static PNG: single-hue
series color, hairline recessive gridlines, ink-toned text (never colored),
a direct label at the extreme instead of a marker on every point, clean
axis formatting. No hover/legend — those are for interactive HTML/SVG
charts, irrelevant here since this is always a plain <img>."""
import base64
import io
from pathlib import Path

from ..observability import observe

_FONTS_DIR = Path(__file__).resolve().parent.parent / "assets" / "fonts"
_ROBOTO_REGISTERED = False


def _ensure_roboto():
    """Registers the vendored Roboto TTFs (src/agent/assets/fonts/, Apache
    2.0) with matplotlib's font manager and sets it as the chart font. Done
    lazily/once — importing matplotlib.font_manager at module import time
    would slow down every import of this module, most of which never
    render a chart at all (e.g. mcp_server's tool discovery)."""
    global _ROBOTO_REGISTERED
    if _ROBOTO_REGISTERED:
        return
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.font_manager as fm

    for f in _FONTS_DIR.glob("Roboto-*.ttf"):
        fm.fontManager.addfont(str(f))
    matplotlib.rcParams["font.family"] = "Roboto"
    _ROBOTO_REGISTERED = True

# Custom brand palette (given hex values). Dark surface stays #212121
# (LibreChat's dark theme, see the entry above); blue is the line/series
# identity, grey doubles as gridline/muted-ink, red/green are reserved for
# the one status use in this chart — the end-of-series direction marker
# below — never used for the line itself, so series identity stays
# consistent regardless of whether the curve happens to be rising or falling.
_SURFACE = "#212121"
_INK_PRIMARY = "#ffffff"
_INK_MUTED = "#c8d6e5"
_GRIDLINE = "#3a3a3a"
_BASELINE = "#4a4a4a"
_SERIES_1 = "#54a0ff"
_RED = "#ff6b6b"
_GREEN = "#1dd1a1"


def _short_x_labels(xs: list[str]) -> list[str]:
    """Full "YYYY-MM-DD HH:MM:SS" on every tick is clutter at minute grain —
    collapse to just HH:MM when the whole series falls on one calendar day
    (the common case: last-hour/last-N-minutes questions), else a short
    "MM-DD HH:MM"."""
    dates = {x.split(" ")[0] for x in xs if " " in x}
    if len(dates) <= 1:
        return [x.split(" ")[1][:5] if " " in x else x for x in xs]
    return [x[5:16].replace(" ", " ") if len(x) >= 16 else x for x in xs]


def _plot(series: list[dict], title: str, x_key: str, y_key: str, chart_type: str) -> bytes:
    _ensure_roboto()
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    import matplotlib.ticker as mticker

    xs_raw = [str(p[x_key]) for p in series]
    ys = [p[y_key] for p in series]
    xs = _short_x_labels(xs_raw)

    fig, ax = plt.subplots(figsize=(7, 3))
    fig.patch.set_facecolor(_SURFACE)
    ax.set_facecolor(_SURFACE)

    if chart_type == "bar":
        ax.bar(xs, ys, color=_SERIES_1, width=0.7)
    else:
        ax.plot(xs, ys, color=_SERIES_1, linewidth=2, solid_capstyle="round", solid_joinstyle="round")
        # direct label at the extreme (peak) instead of a marker on every
        # point — per marks-and-anatomy.md: "label the endpoint, the extreme"
        if ys:
            ax.margins(y=0.2)  # headroom so the peak label clears the title
            if min(ys) >= 0:
                ax.set_ylim(bottom=0)  # concurrency can't be negative — don't imply it can
            peak_i = max(range(len(ys)), key=lambda i: ys[i])
            ax.scatter([xs[peak_i]], [ys[peak_i]], s=64, color=_SERIES_1,
                       edgecolors=_SURFACE, linewidths=2, zorder=3)
            ax.annotate(f"{ys[peak_i]:,.0f}", (xs[peak_i], ys[peak_i]),
                        textcoords="offset points", xytext=(0, 10),
                        ha="center", fontsize=9, color=_INK_PRIMARY)

            # End-of-series direction marker — the one status use of red/green
            # in this chart. Icon (triangle marker, not a text glyph — Roboto
            # doesn't have ▲/▼) + label, not color alone (marks-and-anatomy.md:
            # a status color always ships with an icon/label, never color alone).
            if len(ys) >= 2 and peak_i != len(ys) - 1:
                rising = ys[-1] >= ys[0]
                status_color = _GREEN if rising else _RED
                ax.scatter([xs[-1]], [ys[-1]], s=70, color=status_color,
                           marker="^" if rising else "v",
                           edgecolors=_SURFACE, linewidths=1.5, zorder=3)
                ax.annotate(f"{ys[-1]:,.0f}", (xs[-1], ys[-1]),
                            textcoords="offset points", xytext=(4, -12),
                            ha="left", fontsize=8, color=status_color)

    ax.set_title(title, color=_INK_PRIMARY, fontsize=11, fontweight="bold", loc="left")
    ax.yaxis.set_major_formatter(mticker.FuncFormatter(lambda v, _: f"{v:,.0f}"))
    # thin x-tick labels to ~8 evenly-spaced points — one per point is dense
    # clutter at minute grain (60 points/hour) and reads as noise, not data.
    n = len(xs)
    step = max(1, n // 8)
    tick_idx = list(range(0, n, step))
    ax.set_xticks(tick_idx)
    ax.set_xticklabels([xs[i] for i in tick_idx])
    ax.tick_params(axis="x", rotation=45, labelsize=7, colors=_INK_MUTED)
    ax.tick_params(axis="y", labelsize=8, colors=_INK_MUTED)
    ax.grid(axis="y", color=_GRIDLINE, linewidth=1, linestyle="-", zorder=0)
    ax.set_axisbelow(True)
    for side in ("top", "right"):
        ax.spines[side].set_visible(False)
    for side in ("bottom", "left"):
        ax.spines[side].set_color(_BASELINE)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110, facecolor=_SURFACE)
    plt.close(fig)
    return buf.getvalue()


@observe(as_type="tool")
def render_chart_png(series: list[dict], title: str = "", x_key: str = "minute",
                      y_key: str = "concurrency", chart_type: str = "line") -> bytes:
    """Raw PNG bytes — used by agent.py, which serves the image over HTTP
    (see chart_store.py) instead of embedding it as a data: URI, since chat
    UIs commonly strip data: URIs from markdown image src for security."""
    return _plot(series, title, x_key, y_key, chart_type)


@observe(as_type="tool")
def render_chart(series: list[dict], title: str = "", x_key: str = "minute",
                  y_key: str = "concurrency", chart_type: str = "line") -> str:
    """Base64 data-URI markdown — used by the MCP path (mcp_server/server.py),
    which has no HTTP-serving mechanism of its own. Fine for MCP clients that
    render images from tool output directly rather than through a chat UI's
    markdown sanitizer."""
    png = _plot(series, title, x_key, y_key, chart_type)
    b64 = base64.b64encode(png).decode()
    return f"![{title}](data:image/png;base64,{b64})"
