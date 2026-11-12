from __future__ import annotations

from html import escape
from typing import Iterable

from i18n import t

MAX_TELEGRAM_TEXT = 3500


def code_delivery_messages(header: str, contents: Iterable[str],
                           footer: str | None = None,
                           instructions: str = "") -> list[str]:
    """Split delivered codes into Telegram-safe HTML messages.

    Each code is numbered (1. / 2. / 3. …) so the buyer can confirm the
    quantity matches what they paid for at a glance — especially important
    for multi-quantity orders where 5 codes scroll past as 5 visually-similar
    URLs/keys. Numbering continues across split messages when the run
    overflows Telegram's text-length cap.

    All chrome (the "thank you" footer, the "Instructions:" label, and the
    "codes continued" overflow header) is localized via t() to the current
    i18n language — callers (poller + wallet delivery) set the buyer's language
    on the contextvar before calling. The codes and instruction BODY are data
    and pass through unchanged."""
    if footer is None:
        footer = f"\n\n{t('delivery.footer')}"
    if instructions and instructions.strip():
        footer = (
            f"\n\n{t('delivery.instructions', body=escape(instructions.strip()))}"
            f"{footer}"
        )

    # Materialise once so we can both iterate AND know the total up-front for
    # the number-width calculation. `contents` is typed Iterable for API
    # flexibility, but every caller passes a list.
    items = [str(c) for c in contents]
    total = len(items)
    # Pad single-digit indices to keep the codes left-aligned when the order
    # crosses 10+ items. "  1." / " 10." with a trailing space lines them up
    # neatly inside Telegram's variable-width font.
    width = max(1, len(str(total)))

    messages: list[str] = []
    current = header.rstrip()
    continued_header = t("delivery.codes_continued")

    for idx, content in enumerate(items, start=1):
        num = f"<b>{str(idx).rjust(width)}.</b>"
        line = f"\n{num} <code>{escape(content)}</code>"
        if len(current) + len(line) + len(footer) > MAX_TELEGRAM_TEXT and current != header.rstrip():
            messages.append(current)
            current = continued_header
        current += line

    current += footer
    messages.append(current)
    return messages
