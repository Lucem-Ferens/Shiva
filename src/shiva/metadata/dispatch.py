"""Format detection and the clean+verify step for one file's bytes. Runs inside the worker subprocess."""
from __future__ import annotations

from . import CleanError, CleanOutcome, Unsupported

SUPPORTED = ("jpeg", "png", "pdf", "gif", "webp", "mp3", "flac", "ogg", "opus", "docx", "xlsx", "pptx", "odt", "ods", "odp", "zip")


def detect(data: bytes) -> str | None:
    """Detect by content, not by extension (a renamed file must not be mishandled)."""
    if data[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if data[:5] == b"%PDF-":  # offset 0 only: a zip/tar whose first member is a PDF must not be taken for a PDF
        return "pdf"
    from . import audio, gif, webp
    if gif.sniff(data):
        return "gif"
    if webp.sniff(data):
        return "webp"
    if data[:2] == b"PK":
        from . import office
        return office.sniff(data)   # "docx"|"xlsx"|"pptx"|"odt"|"ods"|"odp"|"zip"; may raise CleanError (hostile archive)
    return audio.sniff(data)   # "mp3" | "flac" | "ogg" | "opus" | None


def clean_and_check(fmt: str, data: bytes, options: dict) -> CleanOutcome:
    """Clean, then prove the content is unchanged. Any failure raises CleanError and nothing is published."""
    if fmt == "jpeg":
        from . import jpeg
        keep_icc = bool(options.get("keep_icc"))
        if options.get("jpeg_mode", "reencode") == "lossless":
            out = jpeg.clean_jpeg(data, keep_orientation=bool(options.get("keep_orientation")), keep_icc=keep_icc)
            jpeg.check_equivalent_jpeg(data, out.data)
        else:
            out = jpeg.reencode_jpeg(data, keep_icc=keep_icc, subsampling=options.get("jpeg_subsampling", "source"))
            jpeg.check_reencoded_jpeg(data, out.data, keep_icc=keep_icc)
    elif fmt == "png":
        from . import png
        out = png.clean_png(data, keep_icc=bool(options.get("keep_icc")))
        png.check_equivalent_png(data, out.data)
    elif fmt == "pdf":
        from . import pdf
        cats = set(options.get("categories") or [])
        out = pdf.clean_pdf(data, cats)
        pdf.check_equivalent_pdf(data, out.data, cats)
    elif fmt == "gif":
        from . import gif
        out = gif.clean_gif(data, keep_icc=bool(options.get("keep_icc")))
        gif.check_equivalent_gif(data, out.data)
    elif fmt == "webp":
        from . import webp
        out = webp.clean_webp(data, keep_icc=bool(options.get("keep_icc")))
        webp.check_equivalent_webp(data, out.data)
    elif fmt in ("mp3", "flac", "ogg", "opus"):
        from . import audio
        out = audio.clean_audio(data, fmt)
        audio.check_equivalent_audio(data, out.data, fmt)
    elif fmt in ("docx", "xlsx", "pptx", "odt", "ods", "odp"):
        from . import office
        um = options.get("unknown_members", "abort")
        # No "categories" key (caller made no choice): the default-ticked set, i.e. everything that does not
        # change content. Comments, tracked changes and glossary are removed only when chosen explicitly.
        cats = options["categories"] if "categories" in options else office.DEFAULT_KEYS
        out = office.clean_office(data, fmt, unknown_members=um, categories=cats)
        office.check_equivalent_office(data, out.data, fmt, unknown_members=um, categories=cats)
    elif fmt == "zip":
        from . import zipclean
        um = options.get("unknown_members", "abort")
        out = zipclean.clean_zip(data, unknown_members=um)
        zipclean.check_equivalent_zip(data, out.data, unknown_members=um)
    else:
        raise Unsupported(f"format not supported: {fmt}")
    if out.data == data:
        out.warnings.append("Nothing to remove: the file was already clean for the checked categories.")
    return out


def scan(fmt: str, data: bytes) -> list[dict]:
    """Categories found, for the selection dialog (PDF and Office/ODF documents)."""
    if fmt == "pdf":
        from . import pdf
        return [dict(key=c.key, label=c.label, detail=c.detail, content_changing=c.content_changing,
                     default_checked=c.default_checked) for c in pdf.scan_pdf(data)]
    if fmt in ("docx", "xlsx", "pptx", "odt", "ods", "odp"):
        from . import office
        return office.scan_office(data, fmt)
    return []
