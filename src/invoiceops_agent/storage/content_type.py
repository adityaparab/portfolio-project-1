"""Content-type sniffing for raw documents (magic bytes, deterministic).

The ingest path knows the upload's declared content type but it is not
persisted alongside the content-addressed object; extraction re-derives it
from the bytes so the vision prompt's data URL is always correct regardless
of what a client declared.
"""

MAGIC: tuple[tuple[bytes, str], ...] = (
    (b"%PDF-", "application/pdf"),
    (b"\x89PNG\r\n\x1a\n", "image/png"),
    (b"\xff\xd8\xff", "image/jpeg"),
    (b"II*\x00", "image/tiff"),
    (b"MM\x00*", "image/tiff"),
)


def sniff_content_type(data: bytes) -> str:
    for magic, content_type in MAGIC:
        if data.startswith(magic):
            return content_type
    return "application/octet-stream"
