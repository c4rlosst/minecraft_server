"""Fit text into a box: wrap it by pixel width and cut it with '...' when it needs more lines than the box has, so text that
changes (a long host name, an error message) can never spill out of its panel onto the controls around it.

`measure` is any function that returns the pixel width of a string (a Tk font's measure, or len(s) * 10 in a test)."""

ELLIPSIS = "..."


def _cut(text: str, measure, width: int) -> str:
    """The longest start of text that fits width (at least one character)."""
    lo = 1
    while lo < len(text) and measure(text[:lo + 1]) <= width:
        lo += 1
    return text[:lo]


def _lines_of(paragraph: str, measure, width: int) -> list:
    lines, current = [], ""
    for word in paragraph.split():
        while measure(word) > width:                       # a word longer than the box (a URL, a hash): break it
            head = _cut(word, measure, width)
            if current:
                lines.append(current)
                current = ""
            lines.append(head)
            word = word[len(head):]
            if not word:
                break
        if not word:
            continue
        trial = word if not current else current + " " + word
        if measure(trial) <= width:
            current = trial
        else:
            lines.append(current)
            current = word
    if current or not lines:
        lines.append(current)
    return lines


def fit_text(text: str, measure, width: int, max_lines: int = 1) -> str:
    """text wrapped to width, at most max_lines lines; when there is more, the last line ends with '...'. Explicit line
    breaks in text are kept."""
    lines = []
    for paragraph in str(text).split("\n"):
        lines += _lines_of(paragraph, measure, width) if paragraph.strip() else [""]
    while len(lines) > 1 and lines[-1] == "":
        lines.pop()
    if len(lines) <= max_lines:
        return "\n".join(lines)
    kept = lines[:max_lines]
    rest_follows = True
    last = kept[-1]
    while last and measure(last + ELLIPSIS) > width:
        last = last[:-1].rstrip()
    kept[-1] = last + ELLIPSIS if rest_follows else last
    return "\n".join(kept)


def one_line(text: str, measure, width: int) -> str:
    return fit_text(text, measure, width, 1)
