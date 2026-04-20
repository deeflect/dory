from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable

from dory_core.token_counting import TokenCounter, build_token_counter


@dataclass(frozen=True, slots=True)
class Chunk:
    chunk_index: int
    start_line: int
    end_line: int
    content: str
    token_count: int


MAX_CHUNK_CHARS = 8000

_token_counter: TokenCounter | None = None


def _count_tokens(text: str) -> int:
    global _token_counter
    if _token_counter is None:
        _token_counter = build_token_counter()
    return _token_counter.count(text)


def _split_oversized_block(
    block_lines: list[str],
    *,
    block_start: int,
    block_end: int,
    max_tokens: int,
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[tuple[int, int, list[str]]]:
    """Break a single block that exceeds max_tokens or max_chars into safe sub-blocks.

    Preserves line ordering and assigns each sub-block a realistic start/end line range.
    Used for log-heavy or machine-generated files that do not respect paragraph breaks.
    """
    total_chars = sum(len(line) + 1 for line in block_lines)
    total_tokens = _count_tokens("\n".join(block_lines))
    if total_tokens <= max_tokens and total_chars <= max_chars:
        return [(block_start, block_end, block_lines)]

    # Target a conservative per-sub-block line budget based on the smaller of
    # the token and char ceilings. Avoid division by zero on empty blocks.
    if not block_lines:
        return [(block_start, block_end, block_lines)]

    avg_chars = max(1, total_chars // len(block_lines))
    lines_per_chunk = max(1, min(max_chars // avg_chars, max_tokens))
    sub_blocks: list[tuple[int, int, list[str]]] = []
    current: list[str] = []
    current_chars = 0
    current_start = block_start

    for offset, line in enumerate(block_lines):
        line_chars = len(line) + 1
        if current and (
            len(current) >= lines_per_chunk or current_chars + line_chars > max_chars
        ):
            end_line = block_start + offset - 1
            sub_blocks.append((current_start, end_line, current))
            current = []
            current_chars = 0
            current_start = block_start + offset
        current.append(line)
        current_chars += line_chars

    if current:
        sub_blocks.append((current_start, block_end, current))

    return sub_blocks


def _split_frontmatter(lines: list[str]) -> tuple[list[str], list[str]]:
    if not lines or lines[0].strip() != "---":
        return [], lines

    for index in range(1, len(lines)):
        if lines[index].strip() == "---":
            return lines[: index + 1], lines[index + 1 :]

    return [], lines


def _iter_blocks(lines: list[str]) -> Iterable[tuple[int, int, list[str]]]:
    block_start = 0
    current: list[str] = []
    current_start = 1

    def flush(end_line: int) -> tuple[int, int, list[str]] | None:
        nonlocal current, current_start
        if not current:
            return None
        block = (current_start, end_line, current)
        current = []
        return block

    for line_number, line in enumerate(lines, start=1):
        is_break = not line.strip()
        is_heading = line.lstrip().startswith("#")
        if current and (is_break or is_heading):
            block = flush(line_number - 1)
            if block is not None:
                yield block
            if is_break:
                current_start = line_number + 1
                continue
            current_start = line_number
        if line.strip():
            if not current:
                current_start = line_number
            current.append(line)
        else:
            current_start = line_number + 1

    block = flush(len(lines))
    if block is not None:
        yield block


def _tail_for_overlap(lines: list[str], overlap_tokens: int) -> list[str]:
    if overlap_tokens <= 0 or not lines:
        return []
    carry: list[str] = []
    running = 0
    for line in reversed(lines):
        carry.insert(0, line)
        running += _count_tokens(line)
        if running >= overlap_tokens:
            break
    return carry


def chunk_markdown(
    text: str,
    max_tokens: int = 800,
    overlap_ratio: float = 0.15,
    max_chars: int = MAX_CHUNK_CHARS,
) -> list[Chunk]:
    lines = text.splitlines()
    frontmatter_lines, body_lines = _split_frontmatter(lines)
    blocks = list(_iter_blocks(body_lines))

    chunks: list[Chunk] = []
    current_lines: list[str] = []
    current_start = 1
    current_token_count = 0
    current_char_count = 0
    chunk_index = 0

    def finalize(end_line: int) -> None:
        nonlocal current_lines, current_start, current_token_count, current_char_count, chunk_index
        if not current_lines:
            return
        content = "\n".join(current_lines).rstrip()
        chunks.append(
            Chunk(
                chunk_index=chunk_index,
                start_line=current_start,
                end_line=end_line,
                content=content,
                token_count=_count_tokens(content),
            )
        )
        chunk_index += 1
        overlap_tokens = int(max_tokens * overlap_ratio) if overlap_ratio > 0 else 0
        carry = _tail_for_overlap(current_lines, overlap_tokens)
        if carry:
            current_lines = list(carry)
            current_token_count = _count_tokens("\n".join(carry))
            current_char_count = sum(len(line) + 1 for line in carry)
            current_start = max(1, end_line - len(carry) + 1)
        else:
            current_lines = []
            current_token_count = 0
            current_char_count = 0

    if frontmatter_lines:
        current_lines.extend(frontmatter_lines)
        current_token_count += _count_tokens("\n".join(frontmatter_lines))
        current_char_count += sum(len(line) + 1 for line in frontmatter_lines)
        current_start = 1

    body_offset = len(frontmatter_lines)
    for start_line, end_line, block_lines in blocks:
        sub_blocks = _split_oversized_block(
            block_lines,
            block_start=start_line,
            block_end=end_line,
            max_tokens=max_tokens,
            max_chars=max_chars,
        )
        for sub_start, sub_end, sub_lines in sub_blocks:
            block_content = "\n".join(sub_lines)
            block_tokens = _count_tokens(block_content)
            block_chars = sum(len(line) + 1 for line in sub_lines)
            block_start_line = body_offset + sub_start

            would_overflow_tokens = current_token_count + block_tokens > max_tokens
            would_overflow_chars = current_char_count + block_chars > max_chars
            if current_lines and (would_overflow_tokens or would_overflow_chars):
                finalize(body_offset + sub_start - 1)
                current_start = block_start_line

            if not current_lines:
                current_start = block_start_line

            current_lines.extend(
                sub_lines if not current_lines else [""] + sub_lines
            )
            current_token_count += block_tokens
            current_char_count += block_chars

            if current_token_count >= max_tokens or current_char_count >= max_chars:
                finalize(body_offset + sub_end)

    if not chunks and not current_lines and text:
        chunks.append(
            Chunk(
                chunk_index=0,
                start_line=1,
                end_line=len(lines) or 1,
                content=text,
                token_count=_count_tokens(text),
            )
        )
        return chunks

    if current_lines:
        finalize(len(lines) or 1)

    if not chunks and not text:
        return [Chunk(chunk_index=0, start_line=1, end_line=1, content="", token_count=0)]

    return chunks
