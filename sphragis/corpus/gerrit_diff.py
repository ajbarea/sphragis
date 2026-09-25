"""Gerrit's `/diff` `content`, computed locally from the two file versions.

The REST route asks Gerrit for the diff of one file between patch sets n and n+1 and cuts
examples out of the `content` blocks it returns. The NoteDb route has the same two blobs and
must cut the same hunks, or a comment anchors to a different region and the example differs.
Git's own histogram diff does not do it: on the AOSP parity sample it split one change's
hunks differently from Gerrit in two of 161 examples, and no combination of git's diff
options reproduced Gerrit's. So this module is a port of exactly what Gerrit runs:

1. JGit `RawText.load`: the binary test, and the line map (lines keep their `\\n`).
2. JGit `RawTextComparator.WS_IGNORE_CHANGE`, because the REST `/diff` default whitespace
   mode is `IGNORE_LEADING_AND_TRAILING` (GetDiff.java) and Gerrit maps that mode to it
   (GitFileDiffCacheImpl.comparatorFor).
3. JGit `DiffAlgorithm.diff` with `HistogramDiff`, falling back to `MyersDiff` where a
   region's repeated lines exceed the chain limit (Gerrit's `HISTOGRAM_WITH_FALLBACK_MYERS`).
4. Gerrit's `DiffContentCalculator.correctForDifferencesInNewlineAtEnd`, and the content
   blocks `DiffInfoCreator.ContentCollector` emits when whitespace is ignored.
5. Gerrit's edits-due-to-rebase (`FileDiffCacheImpl.computeRebaseEdits`, `EditTransformer`,
   `GitPositionTransformer` with `OmitPositionOnConflict`), which marks a block
   `due_to_rebase` when patch sets n and n+1 sit on different parents and the edit is one the
   parents' own diff made.

Ported from JGit master and Gerrit master as read on 2026-09-23. Deliberate fidelity to
quirks is marked where it looks like a bug; changing any of them changes which hunks exist.

Two things are not reproduced. Gerrit decodes a file in the charset juniversalchardet
detects; this decodes UTF-8 and falls back to ISO-8859-1, which differs only for non-UTF-8,
non-ASCII files. And Gerrit retries a diff that exceeds its timeout without the Myers
fallback, which depends on wall-clock time and cannot be reproduced deterministically.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any

_WHITESPACE = frozenset(b" \t\r\n")
_NEWLINE = 0x0A
_CR = 0x0D

#: JGit `RawText.FIRST_FEW_BYTES`, the length the binary test reads in one pass.
_FIRST_FEW_BYTES = 8 * 1024

#: JGit `DiffFormatter`'s default binary file threshold (`PackConfig.DEFAULT_BIG_FILE_THRESHOLD`).
_BINARY_FILE_THRESHOLD = 50 * 1024 * 1024

#: JGit `HistogramDiff.maxChainLength`, and `HistogramDiffIndex.MAX_CNT`.
_MAX_CHAIN_LENGTH = 64
_MAX_CNT = (1 << 8) - 1


# ---------------------------------------------------------------------------
# Edits
# ---------------------------------------------------------------------------


@dataclass
class Edit:
    """JGit `Edit`: the half-open regions [begin_a, end_a) and [begin_b, end_b)."""

    begin_a: int
    end_a: int
    begin_b: int
    end_b: int

    @property
    def kind(self) -> str:
        if self.begin_a < self.end_a:
            return "REPLACE" if self.begin_b < self.end_b else "DELETE"
        return "INSERT" if self.begin_b < self.end_b else "EMPTY"

    def is_empty(self) -> bool:
        return self.begin_a == self.end_a and self.begin_b == self.end_b

    def before(self, cut: Edit) -> Edit:
        return Edit(self.begin_a, cut.begin_a, self.begin_b, cut.begin_b)

    def after(self, cut: Edit) -> Edit:
        return Edit(cut.end_a, self.end_a, cut.end_b, self.end_b)

    def shift(self, amount: int) -> None:
        self.begin_a += amount
        self.end_a += amount
        self.begin_b += amount
        self.end_b += amount


# ---------------------------------------------------------------------------
# JGit RawText under WS_IGNORE_CHANGE
# ---------------------------------------------------------------------------


def _canonical(line: bytes) -> bytes:
    """A line as WS_IGNORE_CHANGE sees it: trailing whitespace dropped, runs made one space.

    `WS_IGNORE_CHANGE.equals` walks two lines byte by byte and skips a whitespace run on both
    sides whenever both stand on one, and `hashRegion` hashes each run as a single space.
    Both are therefore functions of this form, so comparing it is comparing what JGit does.
    """
    end = len(line)
    while end > 0 and line[end - 1] in _WHITESPACE:
        end -= 1
    out = bytearray()
    index = 0
    while index < end:
        byte = line[index]
        index += 1
        if byte in _WHITESPACE:
            while index < end and line[index] in _WHITESPACE:
                index += 1
            byte = 0x20
        out.append(byte)
    return bytes(out)


def _hash(canonical: bytes) -> int:
    """`WS_IGNORE_CHANGE.hashRegion`: djb2 over the canonical form, as a Java int."""
    value = 5381
    for byte in canonical:
        value = (value * 33 + byte) & 0xFFFFFFFF
    return value


class RawText:
    """JGit `RawText`: raw bytes plus the line map, and per-line comparison keys."""

    def __init__(self, content: bytes) -> None:
        self.content = content
        # lineMap: index 0 is padding, lines[i + 1] starts line i, the last entry is the end.
        starts = [0] if content else []
        position = content.find(b"\n")
        while position != -1 and position + 1 < len(content):
            starts.append(position + 1)
            position = content.find(b"\n", position + 1)
        self.lines = [-(2**31), *starts, len(content)]
        self.keys = [
            _canonical(content[self.lines[i + 1] : self.lines[i + 2]]) for i in range(self.size)
        ]
        self.hashes = [_hash(key) for key in self.keys]

    @property
    def size(self) -> int:
        return len(self.lines) - 2

    def missing_newline_at_end(self) -> bool:
        end = self.lines[-1]
        return end == 0 or self.content[end - 1] != _NEWLINE

    def line(self, index: int, charset: str) -> str:
        """`getString(i)`: the line without its `\\n`, decoded."""
        start, end = self.lines[index + 1], self.lines[index + 2]
        if end > start and self.content[end - 1] == _NEWLINE:
            end -= 1
        return self.content[start:end].decode(charset)


def is_binary(data: bytes) -> bool:
    """JGit `RawText.load`'s verdict: too large, or a NUL or a lone CR where it looks.

    A file within `_FIRST_FEW_BYTES` is tested whole, a lone CR at its very end included;
    a larger one is tested on its head and then on every byte by `lineMapOrBinary`.
    """
    if len(data) > _BINARY_FILE_THRESHOLD:
        return True
    if len(data) <= _FIRST_FEW_BYTES:
        return _is_binary_complete(data)
    last = ord("x")
    for current in data:
        if current == 0 or (current != _NEWLINE and last == _CR) or last == 0:
            return True
        last = current
    return last == _CR


def _is_binary_complete(raw: bytes) -> bool:
    """JGit `RawText.isBinary(raw, length, complete=true)`."""
    length = len(raw)
    pointer = -1
    while pointer < length - 2:
        pointer += 1
        current = raw[pointer]
        if current == 0:
            return True
        if current == _CR:
            pointer += 1
            if raw[pointer] != _NEWLINE:
                return True
    if pointer == length - 2:
        pointer += 1
        current = raw[pointer]
        return current == 0 or current == _CR
    return False


def _equal(a: RawText, ai: int, b: RawText, bi: int) -> bool:
    return a.keys[ai] == b.keys[bi]


def _reduce_common_start_end(a: RawText, b: RawText, e: Edit) -> Edit:
    """`RawTextComparator.reduceCommonStartEnd`, byte fast path then the comparator's own.

    The fast path reads `bPtr` from `a`'s line map (a JGit slip); it is only ever called with
    both regions starting at 0, where the two maps agree, so it is kept as written.
    """
    if e.begin_a == e.end_a or e.begin_b == e.end_b:
        return e
    a_raw, b_raw = a.content, b.content
    a_ptr = a.lines[e.begin_a + 1]
    b_ptr = a.lines[e.begin_b + 1]
    a_end = a.lines[e.end_a + 1]
    b_end = b.lines[e.end_b + 1]
    while a_ptr < a_end and b_ptr < b_end and a_raw[a_ptr] == b_raw[b_ptr]:
        a_ptr += 1
        b_ptr += 1
    while a_ptr < a_end and b_ptr < b_end and a_raw[a_end - 1] == b_raw[b_end - 1]:
        a_end -= 1
        b_end -= 1
    e.begin_a = _forward_line(a.lines, e.begin_a, a_ptr)
    e.begin_b = _forward_line(b.lines, e.begin_b, b_ptr)
    e.end_a = _reverse_line(a.lines, e.end_a, a_end)
    partial_a = a_end < a.lines[e.end_a + 1]
    if partial_a:
        b_end += a.lines[e.end_a + 1] - a_end
    e.end_b = _reverse_line(b.lines, e.end_b, b_end)
    if not partial_a and b_end < b.lines[e.end_b + 1]:
        e.end_a += 1
    while e.begin_a < e.end_a and e.begin_b < e.end_b and _equal(a, e.begin_a, b, e.begin_b):
        e.begin_a += 1
        e.begin_b += 1
    while e.begin_a < e.end_a and e.begin_b < e.end_b and _equal(a, e.end_a - 1, b, e.end_b - 1):
        e.end_a -= 1
        e.end_b -= 1
    return e


def _forward_line(lines: Sequence[int], index: int, pointer: int) -> int:
    end = len(lines) - 2
    while index < end and lines[index + 2] < pointer:
        index += 1
    return index


def _reverse_line(lines: Sequence[int], index: int, pointer: int) -> int:
    while index > 0 and pointer <= lines[index]:
        index -= 1
    return index


# ---------------------------------------------------------------------------
# JGit HistogramDiff
# ---------------------------------------------------------------------------


def _table_bits(size: int) -> int:
    bits = size.bit_length() - 1
    if bits == 0:
        bits = 1
    if 1 << bits < size:
        bits += 1
    return bits


class _HistogramIndex:
    """JGit `HistogramDiffIndex`, with its packed records unpacked into parallel lists."""

    def __init__(self, a: RawText, b: RawText, region: Edit) -> None:
        self.a, self.b, self.region = a, b, region
        size = region.end_a - region.begin_a
        bits = _table_bits(size)
        self.table = [0] * (1 << bits)
        self.key_shift = 32 - bits
        self.ptr_shift = region.begin_a
        self.rec_next = [0]
        self.rec_ptr = [0]
        self.rec_cnt = [0]
        self.next = [0] * size
        self.rec_idx = [0] * size
        self.lcs = Edit(0, 0, 0, 0)
        self.cnt = 0
        self.has_common = False

    def _slot(self, text: RawText, index: int) -> int:
        return ((text.hashes[index] * 0x9E370001) & 0xFFFFFFFF) >> self.key_shift

    def find_longest_common_sequence(self) -> Edit | None:
        if not self._scan_a():
            return None
        self.lcs = Edit(0, 0, 0, 0)
        self.cnt = _MAX_CHAIN_LENGTH + 1
        b_ptr = self.region.begin_b
        while b_ptr < self.region.end_b:
            b_ptr = self._try_longest_common_sequence(b_ptr)
        return None if self.has_common and self.cnt > _MAX_CHAIN_LENGTH else self.lcs

    def _scan_a(self) -> bool:
        a = self.a
        for ptr in range(self.region.end_a - 1, self.region.begin_a - 1, -1):
            slot = self._slot(a, ptr)
            chain = 0
            r_idx = self.table[slot]
            found = False
            while r_idx != 0:
                if _equal(a, self.rec_ptr[r_idx], a, ptr):
                    self.next[ptr - self.ptr_shift] = self.rec_ptr[r_idx]
                    self.rec_ptr[r_idx] = ptr
                    self.rec_cnt[r_idx] = min(self.rec_cnt[r_idx] + 1, _MAX_CNT)
                    self.rec_idx[ptr - self.ptr_shift] = r_idx
                    found = True
                    break
                r_idx = self.rec_next[r_idx]
                chain += 1
            if found:
                continue
            if chain == _MAX_CHAIN_LENGTH:
                return False
            self.rec_next.append(self.table[slot])
            self.rec_ptr.append(ptr)
            self.rec_cnt.append(1)
            r_idx = len(self.rec_ptr) - 1
            self.rec_idx[ptr - self.ptr_shift] = r_idx
            self.table[slot] = r_idx
        return True

    def _try_longest_common_sequence(self, b_ptr: int) -> int:
        a, b, region = self.a, self.b, self.region
        b_next = b_ptr + 1
        r_idx = self.table[self._slot(b, b_ptr)]
        while r_idx != 0:
            rec_count = self.rec_cnt[r_idx]
            if rec_count > self.cnt:
                if not self.has_common:
                    self.has_common = _equal(a, self.rec_ptr[r_idx], b, b_ptr)
                r_idx = self.rec_next[r_idx]
                continue
            a_start = self.rec_ptr[r_idx]
            if not _equal(a, a_start, b, b_ptr):
                r_idx = self.rec_next[r_idx]
                continue
            self.has_common = True
            while True:
                next_ptr = self.next[a_start - self.ptr_shift]
                b_start = b_ptr
                a_end = a_start + 1
                b_end = b_start + 1
                rc = rec_count
                while (
                    region.begin_a < a_start
                    and region.begin_b < b_start
                    and _equal(a, a_start - 1, b, b_start - 1)
                ):
                    a_start -= 1
                    b_start -= 1
                    if rc > 1:
                        rc = min(rc, self.rec_cnt[self.rec_idx[a_start - self.ptr_shift]])
                while a_end < region.end_a and b_end < region.end_b and _equal(a, a_end, b, b_end):
                    if rc > 1:
                        rc = min(rc, self.rec_cnt[self.rec_idx[a_end - self.ptr_shift]])
                    a_end += 1
                    b_end += 1
                if b_next < b_end:
                    b_next = b_end
                if self.lcs.end_a - self.lcs.begin_a < a_end - a_start or rc < self.cnt:
                    self.lcs = Edit(a_start, a_end, b_start, b_end)
                    self.cnt = rc
                if next_ptr == 0:
                    break
                while next_ptr < a_end:
                    next_ptr = self.next[next_ptr - self.ptr_shift]
                    if next_ptr == 0:
                        break
                if next_ptr == 0:
                    break
                a_start = next_ptr
            r_idx = self.rec_next[r_idx]
        return b_next


def _histogram(a: RawText, b: RawText, region: Edit) -> list[Edit]:
    """JGit `HistogramDiff.State.diffRegion`, with MyersDiff as the fallback."""
    edits: list[Edit] = []
    queue: list[Edit] = []

    def replace(r: Edit) -> None:
        lcs = _HistogramIndex(a, b, r).find_longest_common_sequence()
        if lcs is None:
            _Myers(edits, a, b, r)
        elif lcs.is_empty():
            edits.append(r)
        else:
            queue.append(r.after(lcs))
            queue.append(r.before(lcs))

    replace(region)
    while queue:
        r = queue.pop()
        kind = r.kind
        if kind in ("INSERT", "DELETE"):
            edits.append(r)
        elif kind == "REPLACE":
            if r.end_a - r.begin_a == 1 and r.end_b - r.begin_b == 1:
                edits.append(r)
            else:
                replace(r)
        else:
            raise AssertionError("an empty region reached the histogram queue")
    return edits


# ---------------------------------------------------------------------------
# JGit MyersDiff
# ---------------------------------------------------------------------------


def _half(value: int) -> int:
    """Java's int division by two, which truncates toward zero."""
    return int(value / 2)


class _Paths:
    """JGit `MyersDiff.MiddleEdit.EditPaths`, one direction."""

    def __init__(self, middle: _Myers, forward: bool) -> None:
        self.m = middle
        self.forward = forward
        self.x: list[int] = []
        self.snakes: list[tuple[int, int]] = []
        self.begin_k = self.end_k = self.middle_k = 0
        self.prev_begin_k = self.prev_end_k = 0
        self.min_k = self.max_k = 0

    def index(self, d: int, k: int) -> int:
        if (d + k - self.middle_k) % 2 != 0:
            raise AssertionError("unexpected odd result")
        return _half(d + k - self.middle_k)

    def get_x(self, d: int, k: int) -> int:
        if k < self.begin_k or k > self.end_k:
            raise AssertionError("k not in range")
        return self.x[self.index(d, k)]

    def get_snake(self, d: int, k: int) -> tuple[int, int]:
        if k < self.begin_k or k > self.end_k:
            raise AssertionError("k not in range")
        return self.snakes[self.index(d, k)]

    def _force(self, k: int) -> int:
        if k < self.min_k:
            return self.min_k + ((k ^ self.min_k) & 1)
        if k > self.max_k:
            return self.max_k - ((k ^ self.max_k) & 1)
        return k

    def initialize(self, k: int, x: int, min_k: int, max_k: int) -> None:
        self.min_k, self.max_k = min_k, max_k
        self.begin_k = self.end_k = self.middle_k = k
        self.x = [x]
        self.snakes = [(x, k + x)]

    def _set(self, index: int, x: int, snake: tuple[int, int]) -> None:
        if index == len(self.x):
            self.x.append(x)
            self.snakes.append(snake)
        elif index < len(self.x):
            self.x[index] = x
            self.snakes[index] = snake
        else:
            raise IndexError(index)

    def snake(self, k: int, x: int) -> int:
        m = self.m
        if self.forward:
            while x < m.end_a and k + x < m.end_b and _equal(m.a, x, m.b, k + x):
                x += 1
            return x
        while x > m.begin_a and k + x > m.begin_b and _equal(m.a, x - 1, m.b, k + x - 1):
            x -= 1
        return x

    def _left(self, x: int) -> int:
        return x if self.forward else x - 1

    def _right(self, x: int) -> int:
        return x + 1 if self.forward else x

    def _better(self, left: int, right: int) -> bool:
        return left > right if self.forward else left < right

    def _adjust(self, k: int, x: int) -> None:
        m = self.m
        other = m.backward if self.forward else m.forward
        hit = (
            (x >= m.end_a or k + x >= m.end_b)
            if self.forward
            else (x <= m.begin_a or k + x <= m.begin_b)
        )
        if hit:
            if k > other.middle_k:
                self.max_k = k
            else:
                self.min_k = k

    def _meets(self, d: int, k: int, x: int, snake: tuple[int, int]) -> bool:
        m = self.m
        if self.forward:
            other = m.backward
            if k < other.begin_k or k > other.end_k:
                return False
            if (d - 1 + k - other.middle_k) % 2 != 0:
                return False
            if x < other.get_x(d - 1, k):
                return False
            m.make_edit(snake, other.get_snake(d - 1, k))
            return True
        other = m.forward
        if k < other.begin_k or k > other.end_k:
            return False
        if (d + k - other.middle_k) % 2 != 0:
            return False
        if x > other.get_x(d, k):
            return False
        m.make_edit(other.get_snake(d, k), snake)
        return True

    def calculate(self, d: int) -> bool:
        self.prev_begin_k, self.prev_end_k = self.begin_k, self.end_k
        self.begin_k = self._force(self.middle_k - d)
        self.end_k = self._force(self.middle_k + d)
        for k in range(self.end_k, self.begin_k - 1, -2):
            left = right = -1
            left_snake = right_snake = (-1, -1)
            if k > self.prev_begin_k:
                i = self.index(d - 1, k - 1)
                left = self.x[i]
                end = self.snake(k - 1, left)
                left_snake = (end, k - 1 + end) if left != end else self.snakes[i]
                if self._meets(d, k - 1, end, left_snake):
                    return True
                left = self._left(end)
            if k < self.prev_end_k:
                i = self.index(d - 1, k + 1)
                right = self.x[i]
                end = self.snake(k + 1, right)
                right_snake = (end, k + 1 + end) if right != end else self.snakes[i]
                if self._meets(d, k + 1, end, right_snake):
                    return True
                right = self._right(end)
            if k >= self.prev_end_k or (k > self.prev_begin_k and self._better(left, right)):
                new_x, new_snake = left, left_snake
            else:
                new_x, new_snake = right, right_snake
            if self._meets(d, k, new_x, new_snake):
                return True
            self._adjust(k, new_x)
            self._set(self.index(d, k), new_x, new_snake)
        return False


class _Myers:
    """JGit `MyersDiff`, appending its edits for `region` to `edits`.

    JGit's `MiddleEdit` keeps the current bounds in fields that every `calculate` call
    overwrites, and `calculateEdits` reads them again after its left recursion has run. The
    right half's starting snake therefore sees the bounds the last nested call left behind.
    The explicit stack below keeps the calls in JGit's order so those reads see the same
    values; an idiomatic rewrite would silently compute different edits.
    """

    def __init__(self, edits: list[Edit], a: RawText, b: RawText, region: Edit) -> None:
        self.edits, self.a, self.b = edits, a, b
        self.begin_a = self.end_a = self.begin_b = self.end_b = 0
        self.edit = Edit(0, 0, 0, 0)
        self.forward = _Paths(self, forward=True)
        self.backward = _Paths(self, forward=False)
        self._initialize(region.begin_a, region.end_a, region.begin_b, region.end_b)
        if self.begin_a >= self.end_a and self.begin_b >= self.end_b:
            return
        self._calculate_edits(self.begin_a, self.end_a, self.begin_b, self.end_b)

    def _initialize(self, begin_a: int, end_a: int, begin_b: int, end_b: int) -> None:
        self.begin_a, self.end_a, self.begin_b, self.end_b = begin_a, end_a, begin_b, end_b
        k = begin_b - begin_a
        self.begin_a = self.forward.snake(k, begin_a)
        self.begin_b = k + self.begin_a
        k = end_b - end_a
        self.end_a = self.backward.snake(k, end_a)
        self.end_b = k + self.end_a

    def make_edit(self, snake1: tuple[int, int], snake2: tuple[int, int]) -> None:
        x1, y1 = snake1
        x2, y2 = snake2
        if x1 > x2 or y1 > y2:
            x1, y1 = x2, y2
        self.edit = Edit(x1, x2, y1, y2)

    def _middle(self, begin_a: int, end_a: int, begin_b: int, end_b: int) -> Edit:
        if begin_a == end_a or begin_b == end_b:
            return Edit(begin_a, end_a, begin_b, end_b)
        self.begin_a, self.end_a, self.begin_b, self.end_b = begin_a, end_a, begin_b, end_b
        min_k, max_k = begin_b - end_a, end_b - begin_a
        self.forward.initialize(begin_b - begin_a, begin_a, min_k, max_k)
        self.backward.initialize(end_b - end_a, end_a, min_k, max_k)
        d = 1
        while True:
            if self.forward.calculate(d) or self.backward.calculate(d):
                return self.edit
            d += 1

    def _calculate_edits(self, begin_a: int, end_a: int, begin_b: int, end_b: int) -> None:
        # Frames: ("call", bounds) runs the head of calculateEdits; ("tail", edit, bounds)
        # runs what follows its left recursion.
        stack: list[tuple[Any, ...]] = [("call", begin_a, end_a, begin_b, end_b)]
        while stack:
            frame = stack.pop()
            if frame[0] == "call":
                _, ba, ea, bb, eb = frame
                edit = self._middle(ba, ea, bb, eb)
                stack.append(("tail", edit, ba, ea, bb, eb))
                if ba < edit.begin_a or bb < edit.begin_b:
                    k = edit.begin_b - edit.begin_a
                    x = self.backward.snake(k, edit.begin_a)
                    stack.append(("call", ba, x, bb, k + x))
                continue
            _, edit, ba, ea, bb, eb = frame
            if edit.kind != "EMPTY":
                self.edits.append(edit)
            if ea > edit.end_a or eb > edit.end_b:
                k = edit.end_b - edit.end_a
                x = self.forward.snake(k, edit.end_a)
                stack.append(("call", x, ea, k + x, eb))


# ---------------------------------------------------------------------------
# JGit DiffAlgorithm.diff, then Gerrit's view of it
# ---------------------------------------------------------------------------


def edits(a: RawText, b: RawText) -> list[Edit]:
    """JGit `DiffAlgorithm.diff` with HistogramDiff under WS_IGNORE_CHANGE."""
    region = _reduce_common_start_end(a, b, Edit(0, a.size, 0, b.size))
    kind = region.kind
    if kind in ("INSERT", "DELETE"):
        return [region]
    if kind == "EMPTY":
        return []
    if region.end_a - region.begin_a == 1 and region.end_b - region.begin_b == 1:
        return [region]
    return _normalize(_histogram(a, b, region), a, b)


def _normalize(found: list[Edit], a: RawText, b: RawText) -> list[Edit]:
    """JGit `DiffAlgorithm.normalize`: slide each insertion or deletion as late as it can go."""
    previous: Edit | None = None
    for current in reversed(found):
        max_a = a.size if previous is None else previous.begin_a
        max_b = b.size if previous is None else previous.begin_b
        if current.kind == "INSERT":
            while (
                current.end_a < max_a
                and current.end_b < max_b
                and _equal(b, current.begin_b, b, current.end_b)
            ):
                current.shift(1)
        elif current.kind == "DELETE":
            while (
                current.end_a < max_a
                and current.end_b < max_b
                and _equal(a, current.begin_a, a, current.end_a)
            ):
                current.shift(1)
        previous = current
    return found


def _newline_corrected(a: RawText, b: RawText, found: list[Edit]) -> list[Edit]:
    """Gerrit `DiffContentCalculator.correctForDifferencesInNewlineAtEnd`."""
    a_size, b_size = a.size, b.size
    if not found and (a_size == 0 or b_size == 0):
        return found
    if not found and a_size != b_size:
        return found
    last = found[-1] if found else None
    if not a.missing_newline_at_end() and b.missing_newline_at_end():
        if last is not None and last.end_a == a_size:
            return [*found[:-1], Edit(last.begin_a, last.end_a + 1, last.begin_b, last.end_b)]
        return [*found, Edit(a_size, a_size + 1, b_size, b_size)]
    if a.missing_newline_at_end() and not b.missing_newline_at_end():
        if last is not None and last.end_b == b_size:
            return [*found[:-1], Edit(last.begin_a, last.end_a, last.begin_b, last.end_b + 1)]
        return [*found, Edit(a_size, a_size, b_size, b_size + 1)]
    return found


def _charset(data: bytes) -> str:
    try:
        data.decode("utf-8")
    except UnicodeDecodeError:
        return "latin-1"
    return "utf-8"


def _source_lines(text: RawText) -> list[str]:
    """Gerrit `TextSource` lines: a file ending in a newline gets a final empty line."""
    charset = _charset(text.content)
    lines = [text.line(i, charset) for i in range(text.size)]
    return lines if text.missing_newline_at_end() else [*lines, ""]


def content_blocks(
    a_lines: Sequence[str],
    b_lines: Sequence[str],
    found: Sequence[Edit],
    due_to_rebase: set[tuple[int, int, int, int]] | None = None,
) -> list[dict[str, Any]]:
    """Gerrit `DiffInfoCreator.ContentCollector` with whitespace ignored.

    Unchanged lines are `ab`; lines the comparator matched whose text differs become
    `{"a", "b", "common": true}`, which `hunks_from_diff` reads as a hunk, as the REST
    corpus did. Empty edits are skipped, as Gerrit skips them. An edit in `due_to_rebase`
    carries `due_to_rebase: true`, the field Gerrit's payload uses.
    """
    blocks: list[dict[str, Any]] = []

    def common(a_from: int, a_to: int, b_from: int) -> None:
        for offset in range(a_to - a_from):
            left, right = a_lines[a_from + offset], b_lines[b_from + offset]
            last = blocks[-1] if blocks else None
            if left == right:
                if last is not None and "ab" in last:
                    last["ab"].append(left)
                else:
                    blocks.append({"ab": [left]})
            elif last is not None and last.get("common"):
                last["a"].append(left)
                last["b"].append(right)
            else:
                blocks.append({"a": [left], "b": [right], "common": True})

    next_a = next_b = 0
    for edit in found:
        if edit.kind == "EMPTY":
            continue
        common(next_a, edit.begin_a, next_b)
        block: dict[str, Any] = {}
        if edit.end_a > edit.begin_a:
            block["a"] = list(a_lines[edit.begin_a : edit.end_a])
        if edit.end_b > edit.begin_b:
            block["b"] = list(b_lines[edit.begin_b : edit.end_b])
        if due_to_rebase and (edit.begin_a, edit.end_a, edit.begin_b, edit.end_b) in due_to_rebase:
            # Compared after the newline correction, as ContentCollector compares: an edit
            # the correction extended no longer equals its tagged original.
            block["due_to_rebase"] = True
        blocks.append(block)
        next_a, next_b = edit.end_a, edit.end_b
    common(next_a, len(a_lines), next_b)
    return blocks


# ---------------------------------------------------------------------------
# Gerrit's edits due to rebase
# ---------------------------------------------------------------------------

EditKey = tuple[int, int, int, int]


def _key(edit: Edit) -> EditKey:
    return (edit.begin_a, edit.end_a, edit.begin_b, edit.end_b)


def _shift_side(positioned: list[EditKey], side: int, mapping: Sequence[Edit]) -> list[EditKey]:
    """`GitPositionTransformer.shiftRangesInOneFile` under `OmitPositionOnConflict`.

    Moves one side of each edit (0 for A, 1 for B) through `mapping`, the diff from that
    side's parent to its patch set. A range that overlaps an edit of the mapping cannot be
    placed and is dropped, which is how Gerrit refuses to call an upstream edit "due to
    rebase" once the change's own edits touch it.
    """
    ranges = sorted(
        {(m.begin_a, m.end_a, m.begin_b, m.end_b) for m in mapping}, key=lambda m: (m[0], m[1])
    )
    if not ranges:
        return positioned
    low, high = 2 * side, 2 * side + 1
    ordered = sorted(positioned, key=lambda e: (e[low], e[high]))

    def moved(edit: EditKey, amount: int) -> EditKey:
        out = list(edit)
        out[low] += amount
        out[high] += amount
        return (out[0], out[1], out[2], out[3])

    shifted = 0
    mapping_index = entity_index = 0
    result: list[EditKey] = []
    while entity_index < len(ordered) and mapping_index < len(ranges):
        edit = ordered[entity_index]
        start, end = edit[low], edit[high]
        old_start, old_end, _, new_end = ranges[mapping_index]
        if old_end <= start:
            shifted = new_end - old_end
            mapping_index += 1
        elif end <= old_start:
            result.append(moved(edit, shifted))
            entity_index += 1
        else:
            entity_index += 1
    result.extend(moved(edit, shifted) for edit in ordered[entity_index:])
    return result


def related(a_parents: Sequence[str], b_parents: Sequence[str], a: str, b: str) -> bool:
    """`DiffUtil.areRelated`: Gerrit looks for rebase edits only when this is false.

    Related means a root or merge commit on either side, one patch set being the other's
    parent, or the two sharing a parent: no rebase separates them.
    """
    if len(a_parents) != 1 or len(b_parents) != 1:
        return True
    return a in b_parents or b in a_parents or bool(set(a_parents) & set(b_parents))


def rebase_edits(
    parent_a: bytes | None, parent_b: bytes | None, before: bytes, after: bytes
) -> tuple[set[EditKey], int]:
    """The parents' own edits placed in the n -> n+1 diff's coordinates, and how many were lost.

    Parent-to-parent edits are moved through parent-a -> patch set n on side A and through
    parent-b -> patch set n+1 on side B; any that collides with the change's own edits on
    either side is dropped, as Gerrit drops it. The second value counts those.
    """
    texts = [RawText(x or b"") for x in (parent_a, parent_b, before, after)]
    pa, pb, a, b = texts
    upstream = [_key(e) for e in edits(pa, pb)]
    placed = _shift_side(upstream, 0, edits(pa, a))
    placed = _shift_side(placed, 1, edits(pb, b))
    return set(placed), len(upstream) - len(placed)


def diff(
    before: bytes | None,
    after: bytes | None,
    *,
    parents: tuple[bytes | None, bytes | None] | None = None,
) -> dict[str, Any]:
    """The REST `/diff` payload `build` reads, for one file between two patch sets.

    `None` is a file absent from that patch set. A binary side yields no content, which
    `build` counts as `no_anchored_hunk`. `parents` is the file in each patch set's parent,
    given only for a step Gerrit treats as a rebase (see `related`); edits the parents made
    are then marked `due_to_rebase`, as Gerrit marks them.
    """
    a_raw, b_raw = before or b"", after or b""
    if is_binary(a_raw) or is_binary(b_raw):
        return {"binary": True, "content": []}
    a, b = RawText(a_raw), RawText(b_raw)
    raw = edits(a, b)
    tagged: set[EditKey] = set()
    # A file absent from either patch set has no rebase edits: Gerrit's position transform
    # finds no file to map the parents' edits onto and omits them.
    comparable = before is not None and after is not None
    if parents is not None and comparable and not any(p and is_binary(p) for p in parents):
        upstream, _ = rebase_edits(parents[0], parents[1], a_raw, b_raw)
        tagged = {_key(e) for e in raw} & upstream
    found = _newline_corrected(a, b, raw)
    blocks = content_blocks(_source_lines(a), _source_lines(b), found, tagged)
    return {"content": blocks}
