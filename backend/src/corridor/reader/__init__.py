"""The quarantined reader (docs/spec/modules.md 5.5, INV-3): the only code that sees untrusted documents."""

from corridor.reader.pasted import MessageSplit, split_message
from corridor.reader.service import ReadResult, load_document, read_pasted, read_upload
from corridor.reader.summary import typed_summary
from corridor.reader.upload import UploadError, process_upload

__all__ = [
    "MessageSplit",
    "ReadResult",
    "UploadError",
    "load_document",
    "process_upload",
    "read_pasted",
    "read_upload",
    "split_message",
    "typed_summary",
]
