"""`lifeos-stt bench`: measures a transcription on the target machine (UC01, ADR 0009).

    lifeos-stt bench <audio> [--reference ref.txt] [--runs 3] [--device cpu|cuda|auto]
                     [--beam 1 5] [--threads 2 4]

For each combination of device, beam size and threads, the same recording is transcribed
`--runs` times with a fresh engine (the real one, model read from the local cache):

- cold load: loading the model on the first run;
- warm real-time factor: model seconds per second of audio, median of the runs;
- WER and CER against the reference text, if given (words and letters compared without
  case, accents nor punctuation);
- peak memory of the process (RSS, it only grows: run one combination per call to read it
  per combination), of the container (cgroup) when readable, and the GPU memory in use
  (`nvidia-smi`) when present;
- the device actually used (`device`, `compute_type`): a CUDA -> CPU fallback is visible.

Output: one JSON line per combination on stdout, then a French summary on stderr. The audio
and the transcript are never printed (only the scores). A dictation is biometric data: run
it inside the container on a read-only mount
(`docker compose run --rm -v /chemin/dictee:/bench:ro stt lifeos-stt bench /bench/dictee.webm
--reference /bench/texte.txt`), and keep it off the repository.
"""

import argparse
import itertools
import json
import logging
import shutil
import statistics
import subprocess
import sys
from collections.abc import Sequence
from pathlib import Path
from typing import Any

from lifeos.config import Settings, get_settings
from lifeos.logging import configure_logging
from lifeos.modules.documents.sniff import sniff
from lifeos.stt.app import peak_memory
from lifeos.stt.engine import Engine, SttFailure, _normalise


def levenshtein(a: Sequence[Any], b: Sequence[Any]) -> int:
    """Edit distance between two sequences (words or letters), two rows of memory."""
    if len(a) < len(b):
        a, b = b, a
    previous = list(range(len(b) + 1))
    for i, left in enumerate(a, start=1):
        current = [i]
        for j, right in enumerate(b, start=1):
            current.append(
                min(previous[j] + 1, current[j - 1] + 1, previous[j - 1] + (left != right))
            )
        previous = current
    return previous[-1]


def _words(text: str) -> list[str]:
    plain = "".join(c if c.isalnum() or c in "'-" else " " for c in _normalise(text))
    return plain.replace("'", " ").split()


def wer(reference: str, hypothesis: str) -> float:
    """Word error rate: word edits per reference word."""
    expected = _words(reference)
    if not expected:
        return 0.0 if not _words(hypothesis) else 1.0
    return levenshtein(expected, _words(hypothesis)) / len(expected)


def cer(reference: str, hypothesis: str) -> float:
    """Character error rate on the same normalised words, spaces included."""
    expected = " ".join(_words(reference))
    if not expected:
        return 0.0 if not _words(hypothesis) else 1.0
    return levenshtein(expected, " ".join(_words(hypothesis))) / len(expected)


def vram_used_mb() -> int | None:
    """GPU memory in use (MiB, first card), when `nvidia-smi` is available."""
    binary = shutil.which("nvidia-smi")
    if binary is None:
        return None
    try:
        done = subprocess.run(  # noqa: S603  (fixed binary and arguments)
            [binary, "--query-gpu=memory.used", "--format=csv,noheader,nounits"],
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return int(done.stdout.split()[0]) if done.returncode == 0 else None
    except (OSError, subprocess.TimeoutExpired, ValueError, IndexError):
        return None


def measure(
    settings: Settings,
    data: bytes,
    mime: str,
    *,
    reference: str | None,
    runs: int,
    device: str,
    beam: int,
    threads: int,
) -> dict[str, Any]:
    tuned = settings.model_copy(
        update={"stt_device": device, "stt_beam_size": beam, "stt_threads": threads}
    )
    engine = Engine(tuned)
    row: dict[str, Any] = {"device_requested": device, "beam": beam, "threads": threads}
    factors: list[float] = []
    totals: list[float] = []
    transcript = None
    load_s: float | None = None
    for _ in range(runs):
        try:
            transcript = engine.transcribe(data, mime=mime, max_seconds=tuned.voice_max_seconds)
        except SttFailure as exc:
            row["error"] = exc.code
            break
        if transcript.load_s is not None:
            load_s = transcript.load_s
        if transcript.duration > 0 and transcript.transcribe_s > 0:
            factors.append(transcript.transcribe_s / transcript.duration)
        totals.append(transcript.decode_s + (transcript.load_s or 0) + transcript.transcribe_s)
    row.update(
        {
            "device": transcript.device if transcript else None,
            "compute_type": transcript.compute_type if transcript else None,
            "audio_seconds": round(transcript.duration, 2) if transcript else None,
            "runs": len(totals),
            "cold_load_s": round(load_s, 2) if load_s is not None else None,
            "rtf": round(statistics.median(factors), 3) if factors else None,
            "rtf_runs": [round(f, 3) for f in factors],
            "seconds_median": round(statistics.median(totals), 2) if totals else None,
            "wer": None,
            "cer": None,
            **peak_memory(),
            "vram_mb": vram_used_mb(),
        }
    )
    if reference is not None and transcript is not None:
        row["wer"] = round(wer(reference, transcript.text), 4)
        row["cer"] = round(cer(reference, transcript.text), 4)
    return row


def _value(value: Any, unit: str = "") -> str:
    return "—" if value is None else f"{value}{unit}"


def _percent(value: float | None) -> float | None:
    return None if value is None else round(value * 100, 1)


def summary(row: dict[str, Any]) -> str:
    return (
        f"- {row['device_requested']} → {_value(row['device'])}"
        f" ({_value(row['compute_type'])}), faisceau {row['beam']}, {row['threads']} fils : "
        f"chargement à froid {_value(row['cold_load_s'], ' s')}, "
        f"facteur temps réel {_value(row['rtf'])}, "
        f"WER {_value(_percent(row['wer']), ' %')}, CER {_value(_percent(row['cer']), ' %')}, "
        f"pic RSS {_value(row['peak_rss_mb'], ' Mo')}, "
        f"conteneur {_value(row['cgroup_peak_mb'], ' Mo')}, "
        f"GPU {_value(row['vram_mb'], ' Mo')}"
        + (f" ; échec {row['error']}" if "error" in row else "")
    )


def main(argv: list[str] | None = None, settings: Settings | None = None) -> int:
    parser = argparse.ArgumentParser(prog="lifeos-stt bench")
    parser.add_argument("audio", type=Path)
    parser.add_argument("--reference", type=Path, help="the text actually read (UTF-8)")
    parser.add_argument("--runs", type=int, default=3)
    parser.add_argument("--device", choices=["cpu", "cuda", "auto"], nargs="+", default=["cpu"])
    parser.add_argument("--beam", type=int, nargs="+", default=None)
    parser.add_argument("--threads", type=int, nargs="+", default=None)
    args = parser.parse_args(argv)
    settings = settings or get_settings()
    configure_logging(settings)
    # stdout carries the JSON lines only: the engine's log lines go to stderr.
    for handler in logging.getLogger().handlers:
        if isinstance(handler, logging.StreamHandler):
            handler.setStream(sys.stderr)
    try:
        data = args.audio.read_bytes()
        reference = args.reference.read_text(encoding="utf-8") if args.reference else None
    except OSError as exc:
        print(f"lecture impossible : {exc.strerror}", file=sys.stderr)
        return 2
    detected = sniff(data)
    if detected is None or detected.kind != "audio":
        print("ce fichier n'est pas un enregistrement audio reconnu", file=sys.stderr)
        return 2
    beams = args.beam or [settings.stt_beam_size]
    threads = args.threads or [settings.stt_threads]
    rows = []
    for device, beam, count in itertools.product(args.device, beams, threads):
        row = measure(
            settings,
            data,
            detected.mime,
            reference=reference,
            runs=max(1, args.runs),
            device=device,
            beam=beam,
            threads=count,
        )
        rows.append(row)
        print(json.dumps(row, ensure_ascii=False), flush=True)
    print(f"{settings.stt_model}, {args.runs} passages par combinaison :", file=sys.stderr)
    for row in rows:
        print(summary(row), file=sys.stderr)
    return 0 if all("error" not in row for row in rows) else 1
