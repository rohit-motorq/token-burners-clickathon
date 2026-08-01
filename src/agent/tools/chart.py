"""Renders a time series server-side. LLM only decides when to call this and
captions the result — it never generates pixels or a chart spec itself."""
import base64
import io

from ..observability import observe


def _plot(series: list[dict], title: str, x_key: str, y_key: str, chart_type: str) -> bytes:
    import matplotlib
    matplotlib.use("Agg")
    import matplotlib.pyplot as plt

    xs = [str(p[x_key]) for p in series]
    ys = [p[y_key] for p in series]

    fig, ax = plt.subplots(figsize=(7, 3))
    if chart_type == "bar":
        ax.bar(xs, ys)
    else:
        ax.plot(xs, ys, marker="o", markersize=2)
    ax.set_title(title)
    ax.tick_params(axis="x", rotation=45, labelsize=6)
    fig.tight_layout()

    buf = io.BytesIO()
    fig.savefig(buf, format="png", dpi=110)
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
