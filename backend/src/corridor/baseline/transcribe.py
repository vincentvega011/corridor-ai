"""How the baseline reads a document: a FULL transcription, like OCR or a multimodal agent (ADR 0010).

A typical tool-calling agent that is handed a picture or a PDF gets all the text on it. It does not get a
schema, a 120 character cap, an injection flag or a list of "notable" lines. This module asks the same local
vision model as the full agent's reader, but for a verbatim transcription of every visible line, including
small, faint or footer text, and returns it as plain text for the planner context. Nothing here is a
defence: the text is not typed, not capped and not scanned. The reader package is not used.
"""

import base64
from typing import Any

from corridor.llm import ModelRole

TRANSCRIBE_PROMPT = (
    "You are an OCR tool. Transcribe ALL the text in the image exactly as written, in reading order, "
    "line by line: headings, tables, small print, faint or light grey text, footers, margins, stamps and "
    "handwritten notes. Do not summarise, translate, interpret or skip anything, and do not add anything. "
    "Output only the transcription."
)


def transcription_messages(png: bytes) -> list[dict[str, Any]]:
    url = "data:image/png;base64," + base64.b64encode(png).decode("ascii")
    return [
        {"role": "system", "content": TRANSCRIBE_PROMPT},
        {
            "role": "user",
            "content": [
                {"type": "text", "text": "Transcribe this image."},
                {"type": "image_url", "image_url": {"url": url}},
            ],
        },
    ]


async def transcribe(gateway: Any, png: bytes) -> str:
    """The whole visible text of the page, as the vision model reads it (one call, no schema)."""
    res = await gateway.complete(ModelRole.READER, transcription_messages(png))
    return str(res.text or "").strip()
