"""Crop the monochrome screenshots in docs/evidence/screens into the figures of the main document.

    uv run --with pillow python make_figures.py

Pure image cropping and stacking: no data is changed. Coordinates are in pixels of the source screenshots."""
from pathlib import Path
from PIL import Image

S = Path(__file__).resolve().parents[2] / "evidence" / "screens"
O = Path(__file__).resolve().parent / "figures"
O.mkdir(exist_ok=True)


def crop(src: str, box: tuple[int, int, int, int], name: str) -> Image.Image:
    im = Image.open(S / src).convert("RGB").crop(box)
    im.save(O / f"{name}.png", optimize=True)
    return im


def stack(parts: list[Image.Image], name: str, gap: int = 24) -> None:
    w = max(p.width for p in parts)
    h = sum(p.height for p in parts) + gap * (len(parts) - 1)
    out = Image.new("RGB", (w, h), "white")
    y = 0
    for p in parts:
        out.paste(p, (0, y))
        y += p.height + gap
    out.save(O / f"{name}.png", optimize=True)


# Figure 1 panels are element screenshots taken by doc_shots.spec.ts.txt (Playwright, 380 px, monochrome theme, after
# a sandbox reset and the CityNest steps); they are copied or stacked here, never edited.
R = O / "raw"
Image.open(R / "card.png").convert("RGB").save(O / "ss_confirm_card.png", optimize=True)
Image.open(R / "hold.png").convert("RGB").save(O / "ss_hold_card.png", optimize=True)
stack([Image.open(R / "bubble.png").convert("RGB"), Image.open(R / "refusal.png").convert("RGB").crop((0, 0, 772, 1005 - 22))], "ss_blocked", gap=16)
side = Image.open(R / "side026.png").convert("RGB")
side.crop((0, 380, side.width, side.height)).save(O / "ss_console_compare.png", optimize=True)
# Figure 4: both panels start at the "2 Check what you received" card and are padded to one height, so they print at one scale
pv = Image.open(S / "33-verifier-valid.en.png").convert("RGB").crop((30, 1550, 968, 2650))
pr = Image.open(S / "34-verifier-forwarded-rejected.en.png").convert("RGB").crop((30, 1212, 968, 2130))
H = max(pv.height, pr.height)
for im, name in ((pv, "ss_pass_valid"), (pr, "ss_pass_rejected")):
    out = Image.new("RGB", (im.width, H), "white")
    out.paste(im, (0, 0))
    out.save(O / f"{name}.png", optimize=True)
crop("console/06-trace-housing-scam.png", (0, 380, 3360, 2060), "ss_console_trace")
# Execution evidence document: one screenshot per scenario where one exists
crop("20-routes.en.png", (30, 1815, 968, 3560), "ev_routes")
crop("13-plan-accepted.en.png", (30, 1506, 968, 3260), "ev_plan")
# E and F console screens were retaken with the same steps as scenarios E and F (doc_shots2.spec.ts.txt, Playwright)
Image.open(R / "E_queue.png").convert("RGB").crop((0, 0, 3360, 900)).save(O / "ev_queue.png", optimize=True)
fc = Image.open(R / "F_chain.png").convert("RGB")
fc.crop((0, 0, fc.width, 1500)).save(O / "ev_chain_fail.png", optimize=True)
