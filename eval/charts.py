"""Tiny dependency free bar charts: one SVG and one PNG per chart (Pillow is already a dependency)."""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

PALETTE = ["#b3261e", "#1b6ca8", "#6b7280", "#d97706", "#2f855a", "#7c3aed", "#0891b2", "#be185d"]


def grouped_bar(
    stem: Path, title: str, groups: list[str], series: dict[str, list[float | None]], note: str = ""
) -> None:
    """Values are fractions 0..1 (None draws nothing). Writes ``<stem>.svg`` and ``<stem>.png``."""
    w, h, left, bottom, top = 1100, 560, 70, 150, 70
    plot_w, plot_h = w - left - 30, h - bottom - top
    gw = plot_w / max(len(groups), 1)
    bw = gw * 0.8 / max(len(series), 1)
    svg = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{w}" height="{h}" viewBox="0 0 {w} {h}" font-family="Helvetica,Arial,sans-serif">',
        f'<rect width="{w}" height="{h}" fill="white"/>',
        f'<text x="{left}" y="32" font-size="20" font-weight="bold">{_x(title)}</text>',
    ]
    img = Image.new("RGB", (w, h), "white")
    d = ImageDraw.Draw(img)
    f_big, f_small = ImageFont.load_default(size=20), ImageFont.load_default(size=13)
    d.text((left, 14), title, fill="black", font=f_big)
    for i in range(0, 6):
        v = i / 5
        y = top + plot_h - v * plot_h
        svg.append(
            f'<line x1="{left}" y1="{y:.1f}" x2="{w - 30}" y2="{y:.1f}" stroke="#e5e7eb"/><text x="{left - 8}" y="{y + 4:.1f}" font-size="12" text-anchor="end">{int(v * 100)}%</text>'
        )
        d.line((left, y, w - 30, y), fill="#e5e7eb")
        d.text((left - 40, y - 7), f"{int(v * 100)}%", fill="black", font=f_small)
    for gi, g in enumerate(groups):
        gx = left + gi * gw + gw * 0.1
        for si, (name, vals) in enumerate(series.items()):
            v = vals[gi]
            color = PALETTE[si % len(PALETTE)]
            if v is not None:
                bh = max(v * plot_h, 1.5 if v == 0 else 0)
                x, y = gx + si * bw, top + plot_h - bh
                svg.append(
                    f'<rect x="{x:.1f}" y="{y:.1f}" width="{bw - 2:.1f}" height="{bh:.1f}" fill="{color}"/>'
                )
                d.rectangle((x, y, x + bw - 2, y + bh), fill=color)
                svg.append(
                    f'<text x="{x + bw / 2:.1f}" y="{y - 4:.1f}" font-size="11" text-anchor="middle">{v * 100:.0f}</text>'
                )
                d.text((x + 2, y - 15), f"{v * 100:.0f}", fill="black", font=f_small)
        lx, ly = gx + gw * 0.4, top + plot_h + 14
        svg.append(
            f'<text x="{lx:.1f}" y="{ly}" font-size="12" transform="rotate(35 {lx:.1f} {ly})">{_x(g)}</text>'
        )
        tmp = Image.new("RGBA", (260, 20), (255, 255, 255, 0))
        ImageDraw.Draw(tmp).text((0, 0), g, fill="black", font=f_small)
        rot = tmp.rotate(-35, expand=True)
        img.paste(rot, (int(gx + gw * 0.2), int(ly)), rot)
    for si, name in enumerate(series):
        x = left + si * 190
        svg.append(
            f'<rect x="{x}" y="{h - 28}" width="14" height="14" fill="{PALETTE[si % len(PALETTE)]}"/><text x="{x + 20}" y="{h - 16}" font-size="13">{_x(name)}</text>'
        )
        d.rectangle((x, h - 28, x + 14, h - 14), fill=PALETTE[si % len(PALETTE)])
        d.text((x + 20, h - 28), name, fill="black", font=f_small)
    if note:
        svg.append(f'<text x="{left}" y="{h - 42}" font-size="12" fill="#555">{_x(note)}</text>')
        d.text((left, h - 48), note, fill="#555555", font=f_small)
    svg.append("</svg>")
    stem.with_suffix(".svg").write_text("\n".join(svg), encoding="utf-8")
    img.save(stem.with_suffix(".png"))


def _x(s: str) -> str:
    return s.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
