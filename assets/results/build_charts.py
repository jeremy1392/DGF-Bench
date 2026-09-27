"""Build the README result charts from results/pilot_2026-09.json.

Run: python assets/results/build_charts.py
Standard library only; no model calls. Writes three SVG files next to this script:
attack_success_by_model.svg, attack_matrix.svg and clean_vs_attack.svg.
"""
from __future__ import annotations

import json
from html import escape
from pathlib import Path
from xml.etree import ElementTree

OUT = Path(__file__).resolve().parent
ROOT = OUT.parents[1]
DATA = ROOT / "results" / "pilot_2026-09.json"

INK = "#142c40"
MUTED = "#506678"
BLUE = "#286b89"
GOLD = "#a36c24"
LINE = "#d5dfe6"
PALE = "#eef4f7"
SURFACE = "#fcfdfd"
FONT = "-apple-system, 'Segoe UI', Roboto, Helvetica, Arial, sans-serif"

FAMILY_LABELS = {"injection": "Injection", "document": "Document vectors",
                 "crowdstrike": "CrowdStrike-derived", "adaptive": "Adaptive"}
FAMILY_COLORS = {"injection": BLUE, "document": GOLD, "crowdstrike": MUTED, "adaptive": INK}
# The three injection kinds that changed decisions or tool budgets in the pilot (results/PILOT_NOTES.md).
BITING_KINDS = [("fake_procedure", "Fake review procedure", GOLD),
                ("exhaustion", "Budget exhaustion", BLUE),
                ("format_confusion", "Format confusion (fake turns)", "#c9a56a")]
CLEAN_COLOR = MUTED


def _hex(rgb):
    return "#%02x%02x%02x" % tuple(max(0, min(255, round(c))) for c in rgb)


def _rgb(color):
    return tuple(int(color[i:i + 2], 16) for i in (1, 3, 5))


def mix(a, b, t):
    """Linear interpolation between two hex colours, t in [0, 1]."""
    return _hex([x + (y - x) * t for x, y in zip(_rgb(a), _rgb(b))])


def rate_color(rate):
    """Sequential warm ramp: any success is tinted; 0 stays on the cool pale surface."""
    if rate <= 0:
        return PALE
    return mix("#f7ecdc", GOLD, 0.18 + 0.82 * rate)


class Figure:
    def __init__(self, width, height, title, description, label, subtitle=None):
        self.width, self.height = width, height
        self.parts = [
            f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}" '
            'role="img" aria-labelledby="title desc">',
            f'<title id="title">{escape(title)}</title><desc id="desc">{escape(description)}</desc>',
            '<defs><pattern id="hatch" width="6" height="6" patternUnits="userSpaceOnUse" patternTransform="rotate(45)">'
            f'<rect width="6" height="6" fill="{PALE}"/><path d="M0 0 V6" stroke="{LINE}" stroke-width="1.5"/></pattern></defs>',
            f'<rect x="0.5" y="0.5" width="{width - 1}" height="{height - 1}" rx="12" fill="{SURFACE}" stroke="{LINE}"/>',
            f'<g font-family="{FONT}">',
        ]
        self.text(40, 38, label, 12, BLUE, weight="700", spacing="2")
        self.text(40, 74, title, 24, INK, weight="700")
        if subtitle:
            self.text(40, 100, subtitle, 15, MUTED)

    def text(self, x, y, text, size=14, color=INK, weight="400", anchor="start", spacing="0", extra=""):
        self.parts.append(f'<text x="{x}" y="{y}" font-size="{size}" fill="{color}" font-weight="{weight}" '
                          f'text-anchor="{anchor}" letter-spacing="{spacing}"{extra}>{escape(text)}</text>')

    def rect(self, x, y, w, h, fill=PALE, stroke="none", radius=0, title=None):
        element = f'<rect x="{x}" y="{y}" width="{w}" height="{h}" rx="{radius}" fill="{fill}" stroke="{stroke}"/>'
        self.parts.append(self._with_title(element, title))

    def path(self, d, color=LINE, width=1, fill="none", title=None):
        element = f'<path d="{d}" fill="{fill}" stroke="{color}" stroke-width="{width}"/>'
        self.parts.append(self._with_title(element, title))

    def hbar(self, x, y, length, h, fill, rounded=True, title=None):
        """Horizontal bar, square at the baseline (left), 4px rounded data end."""
        r = 4 if rounded and length >= 8 else 0
        if length <= 0:
            return
        d = (f"M{x} {y} H{x + length - r} " + (f"a{r} {r} 0 0 1 {r} {r} " if r else "") +
             f"V{y + h - r} " + (f"a{r} {r} 0 0 1 {-r} {r} " if r else "") + f"H{x} Z")
        self.path(d, color="none", fill=fill, title=title)

    def vbar(self, x, baseline, height, w, fill, title=None):
        """Vertical column, square at the baseline, 4px rounded cap."""
        r = 4 if height >= 8 else 0
        if height <= 0:
            return
        top = baseline - height
        d = (f"M{x} {baseline} V{top + r} " + (f"a{r} {r} 0 0 1 {r} {-r} " if r else "") +
             f"H{x + w - r} " + (f"a{r} {r} 0 0 1 {r} {r} " if r else "") + f"V{baseline} Z")
        self.path(d, color="none", fill=fill, title=title)

    def swatch(self, x, y, color, label, size=13, pattern=False):
        fill = "url(#hatch)" if pattern else color
        self.rect(x, y - 10, 14, 12, fill, radius=2)
        self.text(x + 21, y, label, size, MUTED)
        return x + 21 + int(len(label) * size * 0.56) + 22

    def footer(self, *lines):
        top = self.height - 30 - 16 * len(lines)
        self.path(f"M40 {top} H{self.width - 40}")
        for i, line in enumerate(lines):
            self.text(40, top + 22 + i * 16, line, 12.5, MUTED)

    @staticmethod
    def _with_title(element, title):
        if title is None:
            return element
        return f"<g><title>{escape(title)}</title>{element}</g>"

    def save(self, name):
        path = OUT / f"{name}.svg"
        content = "\n".join(self.parts + ["</g></svg>"]) + "\n"
        ElementTree.fromstring(content)  # well-formed XML or raise
        path.write_text(content, encoding="utf-8", newline="\n")
        return path


def load():
    return json.loads(DATA.read_text(encoding="utf-8"))


def model_totals(data):
    """Per model: total attributable, attacked-gate denominator, and the attributable count per family."""
    totals = {}
    for model in data["models"]:
        mid = model["id"]
        per_family = {family: 0 for family in FAMILY_LABELS}
        attacked = 0
        for attack in data["attacks"]:
            cell = attack["cells"].get(mid)
            if cell is None:
                continue
            per_family[attack["family"]] += cell["attributable"]
            attacked += cell["attacked"]
        totals[mid] = {"total": sum(per_family.values()), "attacked": attacked, "families": per_family}
        assert totals[mid]["total"] == data["totals"][mid]["attributable"]
    return totals


def success_by_model(data):
    totals = model_totals(data)
    models = sorted(data["models"], key=lambda m: -totals[m["id"]]["total"])
    n_attacks, n_dossiers = len(data["attacks"]), len(data["setup"]["dossiers"])
    width, height = 1000, 520
    f = Figure(width, height, "Attributable attack successes per model",
               f"Horizontal bars of attributable attack successes per model over {n_attacks} attack kinds on "
               f"{n_dossiers} development dossiers, pilot 2026-09. Values: " +
               "; ".join(f"{m['name']} {totals[m['id']]['total']} of {totals[m['id']]['attacked']} attacked gates"
                         for m in models) + ". Segments show the attack family.",
               "RESULTS / PILOT 2026-09", f"{n_dossiers} dossiers, {n_attacks} attacks, pilot 2026-09")
    x = 40
    for family, label in FAMILY_LABELS.items():
        x = f.swatch(x, 134, FAMILY_COLORS[family], label)
    f.text(width - 40, 134, "attributable successes / attacked gates", 12.5, MUTED, anchor="end")
    x0, x1, top, step, h = 250, 740, 172, 50, 24
    scale = (x1 - x0) / max(totals[m["id"]]["total"] for m in models)
    for i, model in enumerate(models):
        mid, y = model["id"], top + i * step
        total, attacked = totals[mid]["total"], totals[mid]["attacked"]
        f.text(x0 - 16, y + 12, model["name"], 15, INK, weight="700", anchor="end")
        f.text(x0 - 16, y + 28, mid, 11, MUTED, anchor="end")
        f.path(f"M{x0} {y - 6} V{y + h + 6}", LINE)
        x = x0
        segments = [(family, count) for family, count in totals[mid]["families"].items() if count]
        for j, (family, count) in enumerate(segments):
            length = count * scale
            last = j == len(segments) - 1
            gap = 0 if last else 2
            f.hbar(x, y, max(length - gap, 1), h, FAMILY_COLORS[family], rounded=last,
                   title=f"{model['name']}: {count} attributable {FAMILY_LABELS[family].lower()} successes")
            x += length
        f.text(x0 + total * scale + 10, y + 17, str(total), 16, INK, weight="700")
        f.text(x0 + total * scale + 10 + 12 * len(str(total)) + 4, y + 17, f"of {attacked} attacked gates", 12.5, MUTED)
    f.footer("Source: results/pilot_2026-09.json (results/ATTACK_TABLE_UNIFIED.md). Six development dossiers, not the sealed test set.",
             "Attributable: exact injected action, injection observed, not done on the paired clean dossier. "
             "n/a cells (image attack on text-only models) are not counted.")
    return f.save("attack_success_by_model")


def attack_matrix(data):
    models = data["models"]
    totals = model_totals(data)
    families = []
    for attack in data["attacks"]:
        if attack["family"] not in families:
            families.append(attack["family"])
    width = 1000
    name_x, grid_x, col_w, row_h, group_h = 40, 350, 100, 22, 26
    header_y = 140
    grid_y = header_y + 40
    height = grid_y + len(families) * group_h + len(data["attacks"]) * row_h + 150
    f = Figure(width, height, "Attack x model matrix: attributable successes / attacked gates",
               "Heatmap of 29 attacks (rows) by 6 models (columns). Each cell shows attributable successes over "
               "attacked gates and is tinted by their ratio; zero cells are pale; n/a cells are hatched where the "
               "attack cannot be sent to a text-only model. Rows are grouped by family: injection (1-15), document "
               "vectors (16-21), CrowdStrike-derived (22-27) and adaptive (28-29). Totals: " +
               "; ".join(f"{m['name']} {totals[m['id']]['total']}" for m in models) + ".",
               "RESULTS / PILOT 2026-09",
               f"{len(data['setup']['dossiers'])} dossiers, {len(data['attacks'])} attacks, "
               f"cell = attributable / attacked · pilot 2026-09")
    for j, model in enumerate(models):
        cx = grid_x + j * col_w + col_w / 2
        f.text(cx, header_y, model["label"], 13, INK, weight="700", anchor="middle")
        f.text(cx, header_y + 14, model["name"], 10.5, MUTED, anchor="middle")
        f.text(cx, header_y + 27, "image input" if model["image_input"] else "text only", 10.5, MUTED, anchor="middle")
    f.text(name_x, header_y, "#", 11, MUTED, weight="700")
    f.text(name_x + 30, header_y, "Attack", 11, MUTED, weight="700")
    y = grid_y
    first_row = {family: min(a["id"] for a in data["attacks"] if a["family"] == family) for family in families}
    last_row = {family: max(a["id"] for a in data["attacks"] if a["family"] == family) for family in families}
    for family in families:
        f.path(f"M{name_x} {y + 4} H{width - 40}", LINE)
        f.text(name_x, y + 20, f"{FAMILY_LABELS[family].upper()}  ·  rows {first_row[family]}–{last_row[family]}",
               11, BLUE, weight="700", spacing="1")
        y += group_h
        for attack in [a for a in data["attacks"] if a["family"] == family]:
            f.text(name_x, y + 15, str(attack["id"]), 12, MUTED, extra=' font-variant-numeric="tabular-nums"')
            f.text(name_x + 30, y + 15, attack["name"], 12.5, INK)
            for j, model in enumerate(models):
                cell = attack["cells"].get(model["id"])
                x = grid_x + j * col_w + 1
                if cell is None:
                    f.rect(x, y + 1, col_w - 2, row_h - 2, "url(#hatch)", radius=3,
                           title=f"{attack['name']} / {model['name']}: not applicable (text-only model)")
                    f.text(x + (col_w - 2) / 2, y + 15, "n/a", 12, MUTED, anchor="middle")
                    continue
                a, n = cell["attributable"], cell["attacked"]
                rate = a / n if n else 0
                f.rect(x, y + 1, col_w - 2, row_h - 2, rate_color(rate), radius=3,
                       title=f"{attack['name']} / {model['name']}: {a} attributable of {n} attacked gates ({rate:.0%})")
                color = "#ffffff" if rate >= 0.55 else (INK if a else MUTED)
                f.text(x + (col_w - 2) / 2, y + 15, f"{a}/{n}", 12.5, color, weight="700" if a else "400",
                       anchor="middle", extra=' font-variant-numeric="tabular-nums"')
            y += row_h
    f.path(f"M{name_x} {y + 4} H{width - 40}", INK, 1.2)
    f.text(name_x + 30, y + 22, "TOTAL attributable", 12.5, INK, weight="700")
    f.text(name_x + 30, y + 38, "attacked gates", 11, MUTED)
    for j, model in enumerate(models):
        cx = grid_x + j * col_w + col_w / 2
        f.text(cx, y + 22, str(totals[model["id"]]["total"]), 15, INK, weight="700", anchor="middle")
        f.text(cx, y + 38, str(totals[model["id"]]["attacked"]), 11, MUTED, anchor="middle")
    y += 66
    f.text(name_x, y, "Cell tint: share of attacked gates where the attack succeeded attributably", 12.5, MUTED)
    lx = name_x + 462
    f.rect(lx, y - 11, 14, 12, PALE, radius=2)
    f.text(lx + 20, y, "0", 12, MUTED)
    lx += 42
    steps = 12
    for i in range(steps):
        f.rect(lx + i * 12, y - 11, 12, 12, rate_color((i + 0.5) / steps))
    f.text(lx + steps * 12 + 8, y, "100%", 12, MUTED)
    lx += steps * 12 + 52
    f.rect(lx, y - 11, 14, 12, "url(#hatch)", radius=2)
    f.text(lx + 20, y, "n/a: attack impossible for a text-only model", 12, MUTED)
    f.footer("Source: results/pilot_2026-09.json (results/ATTACK_TABLE_UNIFIED.md). Six development dossiers, not the sealed test set.",
             "Attributable: exact injected action, injection observed, not done on the paired clean dossier. "
             "Rows 1-15 full-route runs; rows 16-29 single-gate replays.")
    return f.save("attack_matrix")


def clean_vs_attack(data):
    """Correct decisions LOST to each attack (clean minus attacked). Taller bar = the attack bit harder."""
    models = data["models"]
    block = data["outcome_clean_vs_attack"]
    gates = block["gates"]
    kinds = BITING_KINDS
    # gates_lost = outcome-strict on clean minus outcome-strict under the attack (0 = fully resisted).
    lost, baseline_clean = {}, {}
    for m in models:
        clean = (block["values"][m["id"]]["clean"] or {}).get("outcome_strict")
        baseline_clean[m["id"]] = clean
        row = {}
        for kind, _, _ in kinds:
            attacked = (block["values"][m["id"]].get(kind) or {}).get("outcome_strict")
            row[kind] = None if clean is None or attacked is None else max(0, clean - attacked)
        lost[m["id"]] = row
    top_value = max([v for r in lost.values() for v in r.values() if v is not None] + [1])
    axis_max = max(5, ((top_value + 4) // 5) * 5)
    width, height = 1000, 600
    f = Figure(width, height, "Correct decisions lost to each attack",
               "Grouped bars per model: how many of the clean dossiers' outcome-strict gates the model got wrong once "
               f"the attack was injected (clean minus attacked, out of {gates}). 0 means the model fully resisted; a "
               "taller bar means the attack changed more decisions. Values: " +
               "; ".join(f"{m['name']} " + ", ".join(f"{label} {lost[m['id']][kind]}" for kind, label, _ in kinds)
                         for m in models) + ".",
               "RESULTS / PILOT 2026-09",
               f"Outcome-strict gates lost (of {gates}) when the attack is injected, vs the same clean dossiers · higher = attack bit harder · pilot 2026-09")
    x = 40
    for kind, label, color in kinds:
        x = f.swatch(x, 134, color, label)
    plot_x0, plot_x1, top, baseline = 80, width - 40, 170, 460
    scale = (baseline - top) / axis_max
    for tick in range(0, axis_max + 1, 5):
        yy = baseline - tick * scale
        f.path(f"M{plot_x0} {yy} H{plot_x1}", INK if tick == 0 else LINE, 1)
        f.text(plot_x0 - 10, yy + 4, str(tick), 12, MUTED, anchor="end", extra=' font-variant-numeric="tabular-nums"')
    f.text(plot_x0 - 10, top - 12, "gates lost", 11.5, MUTED, anchor="end")
    f.text(plot_x1, top - 12, "0 = fully resisted", 11.5, MUTED, anchor="end")
    group_w = (plot_x1 - plot_x0) / len(models)
    bar_w, gap = 30, 6
    bars_w = len(kinds) * bar_w + (len(kinds) - 1) * gap
    for i, model in enumerate(models):
        gx = plot_x0 + i * group_w + (group_w - bars_w) / 2
        for k, (kind, label, color) in enumerate(kinds):
            value = lost[model["id"]][kind]
            bx = gx + k * (bar_w + gap)
            if value is None:
                f.text(bx + bar_w / 2, baseline - 6, "n/a", 11, MUTED, anchor="middle")
                continue
            if value == 0:
                f.rect(bx, baseline - 3, bar_w, 3, color)      # a thin marker so "0" is visible
            else:
                f.vbar(bx, baseline, value * scale, bar_w, color,
                       title=f"{model['name']} / {label}: {value} of {gates} correct decisions lost")
            f.text(bx + bar_w / 2, baseline - max(value, 0) * scale - 7, str(value), 12, INK,
                   weight="700" if value else "400", anchor="middle", extra=' font-variant-numeric="tabular-nums"')
        cx = plot_x0 + i * group_w + group_w / 2
        f.text(cx, baseline + 22, model["name"], 13, INK, weight="700", anchor="middle")
        f.text(cx, baseline + 38, model["id"], 10.5, MUTED, anchor="middle")
        clean = baseline_clean[model["id"]]
        f.text(cx, baseline + 56, f"clean: {clean}/{gates} correct" if clean is not None else "clean: n/a", 11.5,
               INK if clean == gates else GOLD, weight="700" if clean is not None and clean < gates / 2 else "400",
               anchor="middle", extra=' font-variant-numeric="tabular-nums"')
    f.footer("Source: results/pilot_2026-09.json. Six development dossiers, not the sealed test set.",
             "Gates lost = correct on the clean dossier, wrong under the attack. A model can only lose what it got right clean.")
    return f.save("clean_vs_attack")


def main():
    data = load()
    for path in (success_by_model(data), attack_matrix(data), clean_vs_attack(data)):
        print(path.relative_to(ROOT).as_posix())


if __name__ == "__main__":
    main()
