"""Builds a self-contained HTML dashboard for LibreChat's Artifacts panel.

Confirmed live (2026-08-02): LibreChat's artifact renderer is a plain
client-side markdown extension that scans ANY assistant message for a
:::artifact{...} fence — it is not gated to LibreChat's native "Agents"
endpoint despite the setup docs implying otherwise. Verified by a hardcoded
test fence through our own endpoints.custom path, rendered correctly in the
side panel. See INNER_CONTEXT.md.

Same "never make the model copy data" pattern as chart.py: the model calls
render_dashboard(title) with no data arguments — agent.py's dispatch layer
assembles the actual HTML from whatever tool results were fetched earlier
in the same turn (dashboard_entries), and the artifact fence is attached to
the reply post-hoc, never passed through the model's own token budget.

Colors are the same 4-hex brand palette as chart.py (blue/red/grey/green) —
stat tiles rotate through them for visual variety unless an entry names its
own accent (e.g. a genuinely negative number should get red on purpose, not
by luck of rotation)."""
import base64
import uuid

from ..observability import observe
from . import chart

_SURFACE = "#212121"
_CARD = "#2a2a2a"
_CARD_BORDER = "#383838"
_INK_PRIMARY = "#ffffff"
_INK_MUTED = "#c8d6e5"
_BLUE = "#54a0ff"
_RED = "#ff6b6b"
_GREEN = "#1dd1a1"
_GREY = "#c8d6e5"
_ACCENT_ROTATION = [_BLUE, _GREEN, _RED, _GREY]


def _stat_card(entry: dict, accent: str) -> str:
    sub = (f'<div style="color:{_INK_MUTED};font-size:12px;margin-top:6px">{entry.get("sub", "")}</div>'
           if entry.get("sub") else "")
    return f"""
    <div style="background:{_CARD};border:1px solid {_CARD_BORDER};border-radius:10px;
                overflow:hidden;min-width:180px">
      <div style="height:4px;background:{accent}"></div>
      <div style="padding:16px 20px 18px">
        <div style="color:{_INK_MUTED};font-size:11px;text-transform:uppercase;letter-spacing:.06em;
                    font-weight:600">{entry['label']}</div>
        <div style="font-size:28px;font-weight:700;margin-top:8px;color:{_INK_PRIMARY};
                    line-height:1.1">{entry['value']}</div>
        {sub}
      </div>
    </div>"""


def _chart_card(entry: dict, accent: str) -> str:
    png = chart.render_chart_png(entry["series"], entry.get("chart_title", ""),
                                  entry.get("x_key", "minute"), entry.get("y_key", "concurrency"),
                                  entry.get("chart_type", "line"))
    b64 = base64.b64encode(png).decode()
    label = entry.get("chart_title", "")
    return f"""
    <div style="background:{_CARD};border:1px solid {_CARD_BORDER};border-radius:10px;
                overflow:hidden;grid-column:1/-1">
      <div style="display:flex;align-items:center;gap:8px;padding:14px 18px 0">
        <span style="width:8px;height:8px;border-radius:50%;background:{accent};display:inline-block"></span>
        <span style="color:{_INK_MUTED};font-size:12px;font-weight:600;text-transform:uppercase;
                     letter-spacing:.05em">{label}</span>
      </div>
      <div style="padding:12px 18px 18px">
        <img src="data:image/png;base64,{b64}" style="width:100%;border-radius:6px;display:block" />
      </div>
    </div>"""


@observe(as_type="tool")
def render_dashboard_html(title: str, entries: list[dict], subtitle: str = "") -> str:
    """entries: list of {"kind": "stat", "label", "value", "sub"?, "accent"?} or
    {"kind": "chart", "series", "x_key", "y_key", "chart_title", "chart_type"?, "accent"?}.
    accent, if given, overrides the automatic color rotation — pass it when
    the color carries real meaning (e.g. red for a genuine decline), not
    just for variety."""
    cards = []
    color_i = 0
    for e in entries:
        accent = e.get("accent") or _ACCENT_ROTATION[color_i % len(_ACCENT_ROTATION)]
        if not e.get("accent"):
            color_i += 1
        if e.get("kind") == "stat":
            cards.append(_stat_card(e, accent))
        elif e.get("kind") == "chart":
            cards.append(_chart_card(e, accent))
    cards_html = "".join(cards)

    subtitle_html = (f'<div style="color:{_INK_MUTED};font-size:13px;margin-top:4px">{subtitle}</div>'
                      if subtitle else "")

    html = f"""<!doctype html>
<html>
<head>
<meta charset="utf-8" />
<style>
  * {{ box-sizing: border-box; }}
  body {{ background:{_SURFACE}; color:{_INK_PRIMARY};
          font-family: -apple-system, "Segoe UI", Roboto, system-ui, sans-serif;
          margin:0; padding:28px; }}
  .header {{ margin-bottom:22px; padding-bottom:16px;
             border-bottom:2px solid {_BLUE}; }}
  h1 {{ font-size:20px; font-weight:700; margin:0; letter-spacing:-.01em; }}
  .grid {{ display:grid; grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
           gap:16px; }}
</style>
</head>
<body>
  <div class="header">
    <h1>{title}</h1>
    {subtitle_html}
  </div>
  <div class="grid">
    {cards_html}
  </div>
</body>
</html>"""

    identifier = f"dashboard-{uuid.uuid4().hex[:8]}"
    return f':::artifact{{identifier="{identifier}" type="text/html" title="{title}"}}\n````\n{html}\n````\n:::'
