"""Generate demo VO (kokoro af_heart) + an ORIGINAL big-beat bed (stable-audio).

Style reference: big-beat / electronic breakbeat in the spirit of 90s big-beat
acts (Crystal Method *feel*: distorted analog bass, filtered synth riff, hard
breakbeat). Original composition only — no samples, no named-track imitation.

Seeds are chosen BEFORE each call and written to a .gen.json sidecar.
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "demo" / "audio"
OUT.mkdir(parents=True, exist_ok=True)

# FAL_KEY from the Hermes .env, never printed
for line in Path.home() / "AppData" / "Local" / "hermes" / ".env".read_text().splitlines():
    if line.startswith("FAL_KEY="):
        os.environ["FAL_KEY"] = line.split("=", 1)[1].strip()
        break

import fal_client  # noqa: E402

VOICE = "af_heart"  # user's preferred buoyant female

LINES = [
    ("vo1", "Every agent user has unshowable work.", 1001),
    ("vo2", "Receipts turns a Hermes session into one document: the ask, every tool call, every failure, every retry.", 1002),
    ("vo3", "Token spend per model, recorded cost, and the path the work actually took.", 1003),
    ("vo4", "No server. No network. One HTML file that opens anywhere.", 1004),
    ("vo5", "Send it to a client. Attach it to a ticket. Keep it as evidence.", 1005),
    ("vo6", "Receipts. Because work you can't show is work nobody trusts.", 1006),
]

BED_PROMPT = ("Original big-beat electronic instrumental, 124 BPM, hard breakbeat drums, "
              "distorted analog synth bass riff, filtered arpeggiated lead, tape-saturated "
              "drum bus, occasional vinyl grit, driving and confident, no vocals, "
              "instrumental only, clean ending")
BED_SEED = 424242


def sidecar(path: Path, prompt: str, seed: int, params: dict, url: str) -> None:
    data = {
        "prompt": prompt,
        "prompt_sha256": hashlib.sha256(prompt.encode()).hexdigest(),
        "seed_requested": seed,
        "params": params,
        "output_url": url,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
        "bytes": path.stat().st_size,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%S%z"),
    }
    path.with_suffix(path.suffix + ".gen.json").write_text(json.dumps(data, indent=2), encoding="utf-8")


def download(url: str, dest: Path) -> None:
    subprocess.run(["curl", "-sSL", url, "-o", str(dest)], check=True)


def dur(p: Path) -> float:
    r = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                        "-of", "default=nw=1:nk=1", str(p)], capture_output=True, text=True)
    try:
        return float(r.stdout.strip())
    except ValueError:
        return 0.0


def main() -> int:
    total = 0.0
    for name, text, seed in LINES:
        dest = OUT / f"{name}.wav"
        if not dest.exists():
            res = fal_client.subscribe("fal-ai/kokoro", arguments={
                "prompt": text, "voice": VOICE, "speed": 1.0, "seed": seed})
            url = res["audio"]["url"]
            download(url, dest)
            sidecar(dest, text, res.get("seed", seed), {"voice": VOICE, "speed": 1.0}, url)
        d = dur(dest)
        total += d
        print(f"{name}: {d:.2f}s  {text[:48]}")

    bed = OUT / "bed.wav"
    if not bed.exists():
        res = fal_client.subscribe("fal-ai/stable-audio-25/text-to-audio", arguments={
            "prompt": BED_PROMPT, "seconds_total": 48, "guidance_scale": 3,
            "num_inference_steps": 8, "seed": BED_SEED})
        url = res["audio"]["url"]
        download(url, bed)
        sidecar(bed, BED_PROMPT, res.get("seed", BED_SEED),
                {"seconds_total": 48, "guidance_scale": 3, "num_inference_steps": 8}, url)
    print(f"bed: {dur(bed):.2f}s")
    print(f"VO total: {total:.2f}s")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
