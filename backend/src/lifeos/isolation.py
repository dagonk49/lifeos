"""Untrusted files are parsed in confined, short-lived child processes (ADR 0008, ADR 0011).

The decoders of the `ml` and `stt` services (PDFium, libjpeg, libpng, libwebp, libheif,
FFmpeg) are large C libraries fed with files from outside. Each parse runs in a child
process forked from a fork server, so that a hostile file can only take that child down:

- memory: the data segment and private mappings are limited (RLIMIT_DATA), so allocations
  fail instead of exhausting the service, which also holds a model;
- out of memory anyway (the container's limit, reached with the model): the child is the
  kernel's first choice (`oom_score_adj` 1000, inherited by what it starts), never the
  service itself;
- time: the parent stops waiting after a deadline, then kills the child together with every
  process it started (its own process group: Tesseract), so nothing keeps running;
- no core dump: a crashing decoder must not write the decrypted file to the disk, nor to
  the host's crash collector.

A child that runs out of its own memory (MemoryError, or a native decoder that aborts when
an allocation fails under the limit) or that a file crashes: a stable outcome of the file.
A child killed (SIGKILL) is another matter: the kernel's OOM killer picks it first when the
container runs short, perhaps because of the model, not of the file. Callers treat that one
as a crash, retried a few times (`ChildDiedError.signal`), never as a verdict on the file.

Nothing is logged here (no content anyway): callers map the outcomes to their error codes.
"""

import multiprocessing
import os
import resource
import signal
from collections.abc import Callable
from contextlib import suppress
from dataclasses import dataclass
from multiprocessing.connection import Connection
from pathlib import Path
from typing import Any

_context = multiprocessing.get_context("forkserver")
# The score the kernel adds when it picks a process to kill (out of memory): the maximum.
OOM_SCORE_ADJ = 1000


class ChildTimeoutError(Exception):
    """The child did not answer in time: it was killed, with what it started."""


class ChildDiedError(Exception):
    """The child died without answering.

    `signal`: the signal that ended it (SIGKILL from the OOM killer or an operator, SIGSEGV
    or SIGABRT from a decoder), or None when it exited without answering (out of its own
    memory while sending the answer).
    """

    def __init__(self, signal: int | None = None) -> None:
        super().__init__(signal)
        self.signal = signal


@dataclass(frozen=True)
class Answer:
    value: Any
    # Peak resident memory of the child plus the largest process it waited for (Tesseract),
    # in MiB: an upper bound, logged to size the service (docs/exploitation.md).
    peak_mb: int


def preload(*modules: str) -> None:
    """Modules the fork server imports once: a child then starts in milliseconds.
    (One list per process: the `ml` and `stt` services each set theirs.)

    `__main__` stays first, as in the default list: the fork server then imports the main
    module (the service's console script) once, and a child does not import it again
    (with FastAPI, uvicorn, the model code: half a second and some 50 MB each)."""
    _context.set_forkserver_preload(["__main__", *modules])


def _confine(memory: int) -> None:
    # Own process group: the parent kills it as a whole (Tesseract included).
    os.setpgid(0, 0)
    # Raising one's own score needs no privilege; /proc may be missing outside Linux.
    with suppress(OSError):
        Path("/proc/self/oom_score_adj").write_text(str(OOM_SCORE_ADJ))
    resource.setrlimit(resource.RLIMIT_CORE, (0, 0))
    # Data segment and private mappings: what the decoders allocate. (The address space
    # limit would break PDFium, which reserves large virtual ranges up front.)
    resource.setrlimit(resource.RLIMIT_DATA, (memory, memory))


def _peak_mb() -> int:
    own = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    waited = resource.getrusage(resource.RUSAGE_CHILDREN).ru_maxrss
    return (own + waited) // 1024  # kilobytes on Linux


def _main(conn: Connection, memory: int, target: Callable[..., Any], args: tuple[Any, ...]) -> None:
    _confine(memory)
    value = target(*args)
    conn.send((value, _peak_mb()))
    conn.close()


def _stop(process: Any) -> None:
    """Kills the child and everything in its process group, then reaps it."""
    with suppress(ProcessLookupError, PermissionError):  # no group yet, or already gone
        os.killpg(process.pid, signal.SIGKILL)
    process.kill()  # in case it had not created its group yet
    process.join(timeout=5)
    if process.exitcode is not None:
        process.close()


def run(
    target: Callable[..., Any], args: tuple[Any, ...], *, memory_mb: int, timeout_s: float
) -> Answer:
    """`target(*args)` in a confined child; returns what it returned (it must be a
    module-level function, and its value picklable). Blocking: run it in a thread.

    Raises ChildTimeoutError or ChildDiedError (with the signal that ended the child, if
    any). The child is always killed and reaped before this returns, with the processes it
    started.
    """
    receiver, sender = _context.Pipe(duplex=False)
    process = _context.Process(
        target=_main, args=(sender, memory_mb * 1024 * 1024, target, args), daemon=True
    )
    process.start()
    sender.close()
    try:
        if not receiver.poll(timeout_s):
            raise ChildTimeoutError
        try:
            value, peak = receiver.recv()
        except EOFError as exc:
            # Closed without an answer: the child has ended, or is about to.
            process.join(timeout=5)
            code = process.exitcode
            raise ChildDiedError(-code if code is not None and code < 0 else None) from exc
    finally:
        receiver.close()
        _stop(process)
    return Answer(value=value, peak_mb=peak)
