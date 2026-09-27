"""A tiny dependency-free SVG writer for the report charts (no matplotlib).

Two charts: a horizontal bar chart (attributable successes per model) and a heatmap
(attacks x models). Both return an SVG string; the caller writes it to disk.
"""
from __future__ import annotations

from html import escape

INK, MUTED, LINE, PALE = "#142c40", "#506678", "#d5dfe6", "#eef4f7"
HOT = "#a3402a"        # strong = attack succeeds
COOL = "#286b89"       # accent
FONT = "font-family='Segoe UI, Helvetica, Arial, sans-serif'"


def _t(x, y, s, size=13, fill=INK, weight="normal", anchor="start"):
    return f"<text x='{x:.1f}' y='{y:.1f}' font-size='{size}' fill='{fill}' font-weight='{weight}' text-anchor='{anchor}' {FONT}>{escape(str(s))}</text>"


def _rect(x, y, w, h, fill, rx=0, stroke="none"):
    return f"<rect x='{x:.1f}' y='{y:.1f}' width='{w:.1f}' height='{h:.1f}' rx='{rx}' fill='{fill}' stroke='{stroke}'/>"


def _svg(width, height, title, desc, body):
    return (f"<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 {width} {height}' width='{width}' height='{height}' "
            f"role='img' aria-labelledby='t d'><title id='t'>{escape(title)}</title><desc id='d'>{escape(desc)}</desc>"
            f"{_rect(0, 0, width, height, '#ffffff')}{body}</svg>\n")


def bar_chart(pairs, title, subtitle):
    """pairs: list of (label, value), drawn as horizontal bars sorted by value descending."""
    pairs = sorted(pairs, key=lambda p: -p[1])
    top = max((v for _, v in pairs), default=1) or 1
    left, right, row_h, y0 = 190, 60, 34, 78
    width, height = 820, y0 + row_h * len(pairs) + 30
    body = [_t(28, 38, title, 20, INK, "bold"), _t(28, 60, subtitle, 13, MUTED)]
    plot_w = width - left - right
    for i, (label, value) in enumerate(pairs):
        y = y0 + i * row_h
        body.append(_t(left - 12, y + 20, label, 13, INK, "normal", "end"))
        body.append(_rect(left, y + 6, plot_w, 20, PALE, 3))
        w = plot_w * value / top
        body.append(_rect(left, y + 6, w, 20, HOT, 3))
        body.append(_t(left + w + 8, y + 20, value, 13, INK, "bold"))
    return _svg(width, height, title, subtitle, "".join(body))


def _cell_colour(attributable, attacked):
    if attacked == 0:
        return "#f3f5f7"
    rate = attributable / attacked
    if rate == 0:
        return PALE
    # PALE -> HOT ramp.
    a, b = (0xee, 0xf4, 0xf7), (0xa3, 0x40, 0x2a)
    mix = tuple(round(a[k] + (b[k] - a[k]) * (0.25 + 0.75 * rate)) for k in range(3))
    return "#%02x%02x%02x" % mix


def heatmap(models, rows, title, subtitle):
    """rows: list of {'name','family','cells':{model:{'attributable','attacked'} | None}}."""
    labels = {"openai/gpt-5.6-sol-pro": "Sol Pro", "google/gemini-3.8-flash": "Gemini",
              "openai/gpt-6-luna-pro": "Luna", "deepseek/deepseek-v4-pro-0813": "DeepSeek",
              "z-ai/glm-5.3": "GLM", "qwen/qwen3-235b-a22b-2507": "Qwen"}
    left, top_h, cell_w, cell_h = 300, 96, 92, 26
    width = left + cell_w * len(models) + 20
    height = top_h + cell_h * len(rows) + 30
    body = [_t(28, 40, title, 20, INK, "bold"), _t(28, 62, subtitle, 13, MUTED)]
    for j, m in enumerate(models):
        x = left + j * cell_w + cell_w / 2
        body.append(_t(x, top_h - 10, labels.get(m, m.split('/')[-1]), 12, INK, "bold", "middle"))
    last_family = None
    for i, row in enumerate(rows):
        y = top_h + i * cell_h
        if row["family"] != last_family:
            body.append(_rect(0, y, width, cell_h * _family_span(rows, i), "none", 0, "none"))
            last_family = row["family"]
        body.append(_t(left - 10, y + cell_h - 8, f"{row['id']}. {row['name']}", 12, INK, "normal", "end"))
        for j, m in enumerate(models):
            x = left + j * cell_w
            cell = row["cells"].get(m)
            if cell is None:
                body.append(_rect(x + 1, y + 1, cell_w - 2, cell_h - 2, "#f3f5f7", 2))
                body.append(_t(x + cell_w / 2, y + cell_h - 8, "n/a", 11, MUTED, "normal", "middle"))
                continue
            body.append(_rect(x + 1, y + 1, cell_w - 2, cell_h - 2, _cell_colour(cell["attributable"], cell["attacked"]), 2, LINE))
            strong = cell["attacked"] and cell["attributable"] / cell["attacked"] > 0.5
            body.append(_t(x + cell_w / 2, y + cell_h - 8, f"{cell['attributable']}/{cell['attacked']}", 11,
                           "#ffffff" if strong else INK, "bold" if cell["attributable"] else "normal", "middle"))
    return _svg(width, height, title, subtitle, "".join(body))


def _family_span(rows, i):
    fam = rows[i]["family"]
    n = 0
    while i + n < len(rows) and rows[i + n]["family"] == fam:
        n += 1
    return n
