"""Word boundary sidecars generated from Microsoft's synthesis events, not ASR."""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

SubtitleFormat = Literal["none", "srt", "vtt"]


class TimingError(Exception):
    """Missing or invalid synthesis boundary events."""


@dataclass(frozen=True)
class Word:
    text: str
    start_ms: int
    end_ms: int

    def as_dict(self) -> dict:
        return {"word": self.text, "start_ms": self.start_ms, "end_ms": self.end_ms}


def read_words(path: Path) -> list[Word]:
    """Convert 100 ns offsets to milliseconds; refuse invalid/nonmonotonic data."""
    words: list[Word] = []
    try:
        with path.open(encoding="utf-8") as stream:
            for line in stream:
                event = json.loads(line)
                if event.get("type") != "WordBoundary":
                    continue
                offset, duration = event["offset"], event["duration"]
                if type(offset) is not int or type(duration) is not int:
                    raise ValueError("invalid offset")
                text = " ".join(html.unescape(event["text"]).split())
                start, end = offset // 10_000, (offset + duration) // 10_000
                if not text or offset < 0 or duration <= 0 or end <= start:
                    raise ValueError("invalid boundary")
                if words and start < words[-1].end_ms:
                    raise ValueError("overlapping boundaries")
                words.append(Word(text, start, end))
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as exc:
        raise TimingError("Word timing metadata is missing or invalid") from exc
    if not words:
        raise TimingError("Microsoft returned no word timing events")
    return words


def _join(words: list[Word]) -> str:
    # No spaces before Western punctuation or between adjacent CJK characters.
    text = " ".join(" ".join(word.text.split()) for word in words)
    text = re.sub(r"\s+([,.!?;:，。！？、；：])", r"\1", text)
    return re.sub(r"(?<=[\u3400-\u9fff\u3040-\u30ff]) (?=[\u3400-\u9fff\u3040-\u30ff])", "", text)


def _timestamp(milliseconds: int, separator: str) -> str:
    seconds, ms = divmod(milliseconds, 1000)
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours:02}:{minutes:02}:{seconds:02}{separator}{ms:03}"


def render_subtitles(words: list[Word], subtitle_format: SubtitleFormat) -> str:
    """Group words into short cues. This is heuristic layout, not transcription."""
    if subtitle_format not in ("srt", "vtt"):
        raise ValueError("Choose srt or vtt")
    groups: list[list[Word]] = []
    current: list[Word] = []
    for word in words:
        if current and (
            len(_join(current + [word])) > 72
            or word.end_ms - current[0].start_ms > 6000
            or word.start_ms - current[-1].end_ms > 700
        ):
            groups.append(current)
            current = []
        current.append(word)
        if re.search(r"[.!?。！？][\"'»”)]?$", word.text):
            groups.append(current)
            current = []
    if current:
        groups.append(current)
    blocks = []
    separator = "," if subtitle_format == "srt" else "."
    for index, group in enumerate(groups, 1):
        # Escape markup and normalize whitespace: source text must not inject cues/tags.
        text = html.escape(_join(group), quote=False)
        start = _timestamp(group[0].start_ms, separator)
        end = _timestamp(group[-1].end_ms, separator)
        blocks.append(f"{index}\n{start} --> {end}\n{text}\n")
    prefix = "WEBVTT\n\n" if subtitle_format == "vtt" else ""
    return prefix + "\n".join(blocks)
