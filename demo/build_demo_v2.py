"""Receipts demo v2 — value-prop card, original big-beat bed, narration.

Keeps every artifact frame from the real capture; only the cards and the audio
are new. VO lands on scene starts and is allowed to cross cuts (documentary
practice) rather than stretching the picture.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path

from PIL import Image, ImageDraw

import build_demo as bd  # reuse canvas/fonts/card generators

ROOT = Path(__file__).resolve().parents[1]
FRAMES = ROOT / "demo" / "frames"
CARDS = ROOT / "demo" / "cards"
AUDIO = ROOT / "demo" / "audio"
OUT = ROOT / "demo" / "receipts-demo-v2.mp4"
CONCAT = ROOT / "demo" / "concat_v2.txt"


def value_card(path: Path) -> None:
    img = bd.canvas()
    d = ImageDraw.Draw(img)
    d.line([(160, 300), (160, 360)], fill=bd.AMBER, width=3)
    d.text((205, 292), "WHY IT MATTERS", font=bd.font(26, True), fill=bd.AMBER)

    rows = [
        ("SHOWABLE WORK", "the session as a document, not a log"),
        ("HONEST FAILURES", "retries rendered as retries, never smoothed over"),
        ("RECORDED COST", "tokens and dollars, per model, per session"),
    ]
    y = 400
    for head, sub in rows:
        d.text((205, y), head, font=bd.font(44, True), fill=bd.INK)
        d.text((205, y + 58), sub, font=bd.font(28), fill=bd.DIM)
        y += 150

    d.text((205, 900), "one command  ·  one file  ·  no network", font=bd.font(26), fill=bd.FAINT)
    img.save(path)


def dur(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def main() -> int:
    CARDS.mkdir(exist_ok=True)
    bd.title_card(CARDS / "t1.png")
    bd.terminal_card(CARDS / "t2.png", [("__CMD__hermes receipts latest", bd.INK)], show_cursor=True)
    bd.terminal_card(CARDS / "t3.png", [("__CMD__hermes receipts latest", bd.INK),
                                        ("receipt → receipts-7f3a91c2demo.html", bd.DIM)], show_cursor=False)
    bd.terminal_card(CARDS / "t4.png", [
        ("__CMD__hermes receipts latest", bd.INK),
        ("receipt → receipts-7f3a91c2demo.html", bd.DIM),
        ("  1 session(s) · 18 tool calls · 4 failure(s) · 3 retry(ies)", bd.INK),
        ("  tokens 280,324 in / 33,765 out · cost $1.259326 (estimated)", bd.INK),
        ("  38 KB · opens offline", bd.DIM),
    ])
    bd.install_card(CARDS / "t5.png")
    value_card(CARDS / "t6.png")
    bd.end_card(CARDS / "t7.png")

    seq: list[tuple[Path, float]] = [
        (CARDS / "t1.png", 3.2),
        (CARDS / "t2.png", 2.4),
        (CARDS / "t3.png", 2.0),
        (CARDS / "t4.png", 4.2),
    ]

    def add(name: str, secs: float) -> None:
        p = FRAMES / f"{name}.png"
        if p.exists():
            seq.append((p, secs))

    add("00_top", 3.4)
    add("01_ask_a", 0.16)
    add("02_ask_b", 0.16)
    add("03_ask_hold", 3.2)
    for p in sorted(FRAMES.glob("04_retry_*.png")):
        seq.append((p, 0.13))
    add("05_retry_hold", 3.8)
    for p in sorted(FRAMES.glob("06_fail_*.png")):
        seq.append((p, 0.13))
    add("07_fail_hold", 2.4)
    add("08_jump_highlight", 4.4)
    for p in sorted(FRAMES.glob("09_spend_*.png")):
        seq.append((p, 0.13))
    add("10_spend_hold", 3.6)
    add("11_recon_hold", 3.6)
    seq += [(CARDS / "t5.png", 5.0), (CARDS / "t6.png", 5.2), (CARDS / "t7.png", 4.4)]

    starts, t = {}, 0.0
    lines = []
    for p, s in seq:
        lines.append(f"file '{p.as_posix()}'\nduration {s:.3f}")
        t += s
    total = t
    CONCAT.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"{len(seq)} frames, {total:.1f}s")

    # scene starts for VO placement
    t = 0.0
    for p, s in seq:
        starts.setdefault(p.stem, t)
        t += s
    for key in ("t1", "00_top", "05_retry_hold", "08_jump_highlight", "10_spend_hold", "t5", "t6", "t7"):
        starts.setdefault(key, None)

    vo = [AUDIO / f"vo{i}.wav" for i in range(1, 7)]
    vdur = [dur(p) for p in vo]

    # placements: (offset, clip) — VO may cross cuts
    place = [
        (0.3, vo[0]),                                  # title
        (starts["t2"], vo[1]),                         # command
        (starts["05_retry_hold"], vo[2]),              # retries/failures
        (starts["10_spend_hold"], vo[3]),              # spend + reconciliation
        (starts["t5"], vo[4]),                         # install
        (starts["t7"] + 0.2, vo[5]),                   # end card
    ]
    for (off, clip), d in zip(place, vdur):
        assert off + d <= total + 0.5, f"{clip.name} overruns video ({off:.1f}+{d:.1f} > {total:.1f})"

    # ---- video (silent) ----
    silent = ROOT / "demo" / "_video_v2.mp4"
    subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(CONCAT),
                    "-fps_mode", "vfr", "-vf", "scale=trunc(iw/2)*2:trunc(ih/2)*2",
                    "-pix_fmt", "yuv420p", "-c:v", "libx264", "-crf", "18",
                    str(silent)], check=True, capture_output=True)

    # ---- audio graph ----
    ins = ["-i", str(AUDIO / "bed.wav")]
    for p in vo:
        ins += ["-i", str(p)]
    parts = ["[0:a]aformat=sample_rates=48000:channel_layouts=stereo,volume=0.20[bed]"]
    mix_labels = ["[bed]"]
    for i, (off, clip) in enumerate(place, start=1):
        ms = int(round(off * 1000))
        parts.append(f"[{i}:a]aformat=sample_rates=48000:channel_layouts=stereo,"
                     f"volume=1.9,adelay={ms}|{ms}[v{i}]")
        mix_labels.append(f"[v{i}]")
    parts.append("".join(mix_labels) + f"amix=inputs={len(mix_labels)}:normalize=0,"
                 "alimiter=limit=0.95,loudnorm=I=-16:TP=-1.5:LRA=11,afade=t=out:st=%.2f:d=1.0[aout]"
                 % (total - 1.0))
    audio_mix = ROOT / "demo" / "_mix_v2.wav"
    subprocess.run(["ffmpeg", "-y", *ins, "-filter_complex", ";".join(parts),
                    "-map", "[aout]", "-t", f"{total:.3f}", str(audio_mix)],
                   check=True, capture_output=True)

    subprocess.run(["ffmpeg", "-y", "-i", str(silent), "-i", str(audio_mix),
                    "-c:v", "copy", "-c:a", "aac", "-b:a", "192k", "-shortest",
                    "-movflags", "+faststart", str(OUT)], check=True, capture_output=True)

    print(json.dumps({"out": str(OUT), "seconds": round(dur(OUT), 2),
                      "vo": [round(x, 2) for x in vdur],
                      "placements": [(round(o, 2), c.name) for o, c in place]}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
