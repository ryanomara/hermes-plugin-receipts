"""Assemble the demo video from real captures + generated title/terminal cards.

Artifact frames are real screenshots of the rendered receipt (demo/frames/*.png).
Cards are drawn here from the actual CLI output string, so nothing on screen is
invented. Output: demo/receipts-demo.mp4, 1920x1080, under 90 seconds.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

ROOT = Path(__file__).resolve().parents[1]
FRAMES = ROOT / "demo" / "frames"
OUT = ROOT / "demo" / "receipts-demo.mp4"

W, H = 2160, 1215  # 1440x810 at 1.5x
BG = (7, 8, 10)
PANEL = (13, 16, 19)
INK = (219, 226, 232)
DIM = (133, 147, 160)
FAINT = (90, 104, 115)
CYAN = (58, 214, 207)
AMBER = (255, 176, 32)
RED = (255, 82, 82)
GREEN = (91, 217, 141)
LINE = (30, 38, 46)

FONT_DIR = Path("C:/Windows/Fonts")
MONO = FONT_DIR / "consola.ttf"
MONO_B = FONT_DIR / "consolab.ttf"


def font(size: int, bold: bool = False):
    path = MONO_B if bold else MONO
    try:
        return ImageFont.truetype(str(path), size)
    except OSError:
        return ImageFont.load_default()


def canvas() -> Image.Image:
    img = Image.new("RGB", (W, H), BG)
    d = ImageDraw.Draw(img)
    # scanlines, matching the artifact's texture
    for y in range(0, H, 3):
        d.line([(0, y), (W, y)], fill=(9, 10, 12))
    return img


def wrap(draw, text, fnt, max_w):
    words, lines, cur = text.split(" "), [], ""
    for word in words:
        trial = (cur + " " + word).strip()
        if draw.textlength(trial, font=fnt) <= max_w:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = word
    if cur:
        lines.append(cur)
    return lines


def title_card(path: Path) -> None:
    img = canvas()
    d = ImageDraw.Draw(img)
    d.line([(160, 470), (160, 640)], fill=CYAN, width=3)
    d.text((205, 470), "RECEIPTS", font=font(96, True), fill=INK)
    d.text((205, 585), "a session flight recorder for Hermes", font=font(34), fill=DIM)
    d.text((205, 700), "one command  ·  one self-contained HTML file  ·  opens offline",
           font=font(26), fill=FAINT)
    img.save(path)


def terminal_card(path: Path, lines: list[tuple[str, tuple]], show_cursor: bool = False,
                  prompt: str = "~/Projects/demo-storefront $ ") -> None:
    img = canvas()
    d = ImageDraw.Draw(img)
    pad, top = 120, 150
    width, height = W - 2 * pad, H - 2 * top
    d.rectangle([pad, top, pad + width, top + height], fill=PANEL, outline=LINE, width=2)
    # chrome
    d.line([(pad, top + 54), (pad + width, top + 54)], fill=LINE, width=2)
    for i, c in enumerate((RED, AMBER, GREEN)):
        cx = pad + 34 + i * 30
        d.ellipse([cx, top + 20, cx + 14, top + 34], fill=c)
    d.text((pad + 128, top + 17), "hermes", font=font(22), fill=FAINT)

    y = top + 92
    x = pad + 40
    d.text((x, y), prompt, font=font(31), fill=GREEN)
    x += d.textlength(prompt, font=font(31))
    for text, colour in lines:
        if text.startswith("__CMD__"):
            d.text((x, y), text[7:], font=font(31), fill=INK)
            x += d.textlength(text[7:], font=font(31))
        else:
            y += 52
            d.text((pad + 40, y), text, font=font(28), fill=colour)
    if show_cursor:
        d.rectangle([x + 4, y + 6, x + 18, y + 40], fill=INK)
    img.save(path)


def install_card(path: Path) -> None:
    img = canvas()
    d = ImageDraw.Draw(img)
    d.text((160, 420), "install", font=font(26), fill=FAINT)
    cmd = "hermes plugins install <owner>/hermes-plugin-receipts"
    d.text((160, 470), cmd, font=font(46, True), fill=CYAN)
    d.text((160, 570), "no API keys  ·  no config  ·  no pip dependencies",
           font=font(30), fill=DIM)
    img.save(path)


def end_card(path: Path) -> None:
    img = canvas()
    d = ImageDraw.Draw(img)
    d.text((160, 500), "Receipts", font=font(84, True), fill=INK)
    d.text((160, 620), "every agent user has unshowable work", font=font(34), fill=DIM)
    d.text((160, 700), "@NousResearch", font=font(30), fill=CYAN)
    img.save(path)


def main() -> int:
    FRAMES.mkdir(parents=True, exist_ok=True)
    cards = ROOT / "demo" / "cards"
    cards.mkdir(exist_ok=True)

    title_card(cards / "t1.png")
    terminal_card(cards / "t2.png", [("__CMD__hermes receipts latest", INK)], show_cursor=True)
    terminal_card(cards / "t3.png", [("__CMD__hermes receipts latest", INK),
                                     ("receipt → receipts-7f3a91c2demo.html", DIM)], show_cursor=False)
    terminal_card(cards / "t4.png", [
        ("__CMD__hermes receipts latest", INK),
        ("receipt → receipts-7f3a91c2demo.html", DIM),
        ("  1 session(s) · 18 tool calls · 4 failure(s) · 3 retry(ies)", INK),
        ("  tokens 280,324 in / 33,765 out · cost $1.259326 (estimated)", INK),
        ("  38 KB · opens offline", DIM),
    ])
    install_card(cards / "t5.png")
    end_card(cards / "t6.png")

    # (file, seconds) — real captures keep scroll motion; cards hold long enough to read
    seq: list[tuple[Path, float]] = [
        (cards / "t1.png", 2.6),
        (cards / "t2.png", 2.2),
        (cards / "t3.png", 1.6),
        (cards / "t4.png", 3.8),
    ]

    def add(name: str, secs: float) -> None:
        p = FRAMES / f"{name}.png"
        if p.exists():
            seq.append((p, secs))

    add("00_top", 3.4)
    add("01_ask_a", 0.16)
    add("02_ask_b", 0.16)
    add("03_ask_hold", 3.2)

    retry = sorted(FRAMES.glob("04_retry_*.png"))
    for p in retry:
        seq.append((p, 0.13))
    add("05_retry_hold", 3.8)

    fails = sorted(FRAMES.glob("06_fail_*.png"))
    for p in fails:
        seq.append((p, 0.13))
    add("07_fail_hold", 2.4)
    add("08_jump_highlight", 4.4)

    spend = sorted(FRAMES.glob("09_spend_*.png"))
    for p in spend:
        seq.append((p, 0.13))
    add("10_spend_hold", 3.6)
    add("11_recon_hold", 3.6)

    seq += [(cards / "t5.png", 3.2), (cards / "t6.png", 2.8)]

    total = sum(s for _, s in seq)
    print(f"{len(seq)} frames, {total:.1f}s")

    listing = ROOT / "demo" / "concat.txt"
    with listing.open("w", encoding="utf-8", newline="\n") as fh:
        for path in seq:
            fh.write("file '%s'\n" % str(path[0]).replace("\\", "/"))
            fh.write("duration %.3f\n" % path[1])
        fh.write("file '%s'\n" % str(seq[-1][0]).replace("\\", "/"))

    cmd = [
        "ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(listing),
        "-vf", "scale=1920:1080:flags=lanczos,fps=30,format=yuv420p",
        "-c:v", "libx264", "-preset", "medium", "-crf", "18",
        "-movflags", "+faststart", str(OUT),
    ]
    print(" ".join(cmd))
    res = subprocess.run(cmd, capture_output=True, text=True)
    if res.returncode != 0:
        print(res.stderr[-3000:], file=sys.stderr)
        return res.returncode
    print("wrote", OUT, f"{OUT.stat().st_size / 1_048_576:.1f} MB")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
