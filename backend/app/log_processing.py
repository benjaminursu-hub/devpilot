"""Turn GitHub's log archive (ZIP) into one readable, bounded text blob.

The archive is untrusted input, so reading is bounded: we stream each file and keep only its
tail, and give up if the archive expands to more than MAX_UNCOMPRESSED_BYTES (zip bomb guard).
"""

import io
import re
import zipfile
from dataclasses import dataclass

from app.errors import LogArchiveError

MAX_UNCOMPRESSED_BYTES = 300 * 1024 * 1024
_CHUNK = 64 * 1024

_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;?]*[ -/]*[@-~]")
# GitHub prefixes every line with an ISO timestamp, e.g. "2026-11-07T12:00:01.1234567Z ".
_LINE_TIMESTAMP = re.compile(r"^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(?:\.\d+)?Z ?", re.MULTILINE)
_LEADING_NUMBER = re.compile(r"^(\d+)_")


@dataclass(frozen=True)
class LogFile:
    name: str  # e.g. "build" for "0_build.txt"
    text: str


def extract_log_files(archive: bytes, *, keep_bytes_per_file: int) -> tuple[list[LogFile], bool]:
    """Return (files in run order, whether any file had to be cut).

    GitHub archives contain one top-level `N_<job>.txt` per job (the full job log) plus
    folders with per-step files. We use the top-level job files when present, to avoid
    duplicating every line, and fall back to the step files otherwise.
    """
    try:
        zf = zipfile.ZipFile(io.BytesIO(archive))
    except zipfile.BadZipFile as exc:
        raise LogArchiveError("GitHub returned a log archive that is not a valid ZIP.") from exc

    with zf:
        entries = [i for i in zf.infolist() if not i.is_dir() and i.filename.endswith(".txt")]
        job_files = [i for i in entries if "/" not in i.filename]
        chosen = sorted(job_files or entries, key=_sort_key)

        files: list[LogFile] = []
        cut = False
        budget = MAX_UNCOMPRESSED_BYTES
        for info in chosen:
            data, read, was_cut = _read_tail(zf, info, keep_bytes_per_file, budget)
            budget -= read
            cut = cut or was_cut
            files.append(LogFile(name=_display_name(info.filename), text=_clean(data)))
        return files, cut


def combine_log_files(files: list[LogFile]) -> str:
    return "\n".join(f"===== {f.name} =====\n{f.text.rstrip()}\n" for f in files)


def truncate_keep_tail(text: str, max_chars: int) -> tuple[str, bool]:
    """Keep the END of the log: build failures are almost always reported last."""
    if len(text) <= max_chars:
        return text, False
    tail = text[-max_chars:]
    # Start at a line boundary so the first line isn't half a line.
    newline = tail.find("\n")
    if 0 <= newline < 500:
        tail = tail[newline + 1 :]
    omitted = len(text) - len(tail)
    return f"[... {omitted} earlier characters omitted ...]\n{tail}", True


# --- helpers ----------------------------------------------------------------------------------


def _read_tail(
    zf: zipfile.ZipFile, info: zipfile.ZipInfo, keep_bytes: int, budget: int
) -> tuple[bytes, int, bool]:
    buffer = bytearray()
    read = 0
    cut = False
    with zf.open(info) as handle:
        while chunk := handle.read(_CHUNK):
            read += len(chunk)
            if read > budget:
                raise LogArchiveError("The log archive is too large to process.")
            buffer += chunk
            if len(buffer) > keep_bytes:
                del buffer[: len(buffer) - keep_bytes]
                cut = True
    return bytes(buffer), read, cut


def _clean(data: bytes) -> str:
    text = data.decode("utf-8", errors="replace").lstrip("﻿")
    text = text.replace("\r\n", "\n")
    text = _ANSI_ESCAPE.sub("", text)
    return _LINE_TIMESTAMP.sub("", text)


def _sort_key(info: zipfile.ZipInfo) -> tuple:
    parts = info.filename.split("/")
    key = []
    for part in parts:
        match = _LEADING_NUMBER.match(part)
        key.append((int(match.group(1)) if match else 10**9, part))
    return tuple(key)


def _display_name(filename: str) -> str:
    parts = [_LEADING_NUMBER.sub("", part) for part in filename.split("/")]
    parts[-1] = parts[-1].removesuffix(".txt")
    return " / ".join(parts)
