"""Optional PDF "flatten pages to images" (category key "flatten" in pdf.py).

Limit of the visual check: original and result are both rendered by the same PDFium, so content PDFium itself fails to
draw cannot be caught. Output size is not capped (a crafted file can exceed the worker memory cap and fail safely).

flatten_pdf(data)              -> bytes of a new PDF that holds only one JPEG image per page
check_flattened_pdf(orig, new) -> raises CleanError unless `new` is a faithful image-only copy of `orig`

Each page is rendered by PDFium (pypdfium2, BSD-3-Clause/Apache-2.0) exactly as a viewer shows it (crop box,
rotation, visible annotations and form appearances burned in), then placed as an image on a fresh page of the same
size. Text under a black box is gone because only the pixels are kept. Text becomes unselectable and unsearchable;
links, bookmarks, forms and attachments are lost; the file is usually larger.

The checker is written independently of the flattener (it does not reuse its helpers): it inspects the output
structure with pikepdf and compares a fresh rendering of the original with a rendering of the output, so a blank or
shifted page cannot pass."""
from __future__ import annotations

import io
import os

import pikepdf

from . import CleanError

DPI = 150
MAX_PAGES = 200                    # keeps render + check well inside the worker timeout and memory limit
MAX_PAGE_PIXELS = 16_000_000       # larger pages are rendered at a lower resolution
JPEG_QUALITY = 90


def _pdfium():
    try:
        import pypdfium2
    except ImportError as e:  # pragma: no cover - depends on the installation
        raise CleanError("Flattening needs pypdfium2, which is not available.") from e
    return pypdfium2


def _scale(w_pt: float, h_pt: float) -> float:
    s = DPI / 72.0
    px = (w_pt * s) * (h_pt * s)
    if px > MAX_PAGE_PIXELS:
        s *= (MAX_PAGE_PIXELS / px) ** 0.5
    return s


def flatten_pdf(data: bytes) -> bytes:
    pdfium = _pdfium()
    try:
        doc = pdfium.PdfDocument(data)
    except pdfium.PdfiumError as e:
        raise CleanError(f"Cannot render PDF: {e}") from e
    try:
        n = len(doc)
        if n == 0:
            raise CleanError("The PDF has no pages to flatten.")
        if n > MAX_PAGES:
            raise CleanError(f"Too many pages to flatten ({n}; the limit is {MAX_PAGES}).")
        out = pikepdf.new()
        for i in range(n):
            page = doc[i]
            try:
                w, h = page.get_size()
                bitmap = page.render(scale=_scale(w, h), fill_color=(255, 255, 255, 255), may_draw_forms=True)
                img = bitmap.to_pil().convert("RGB")
            finally:
                page.close()
            buf = io.BytesIO()
            img.save(buf, "JPEG", quality=JPEG_QUALITY)   # Pillow writes no Exif or comment unless asked
            xobj = pikepdf.Stream(out, buf.getvalue())
            xobj.Type, xobj.Subtype = pikepdf.Name.XObject, pikepdf.Name.Image
            xobj.Width, xobj.Height = img.width, img.height
            xobj.ColorSpace, xobj.BitsPerComponent = pikepdf.Name.DeviceRGB, 8
            xobj.Filter = pikepdf.Name.DCTDecode
            content = f"q {w:.4f} 0 0 {h:.4f} 0 0 cm /Im0 Do Q".encode("ascii")
            pg = pikepdf.Dictionary(
                Type=pikepdf.Name.Page,
                MediaBox=pikepdf.Array([0, 0, round(w, 4), round(h, 4)]),
                Resources=pikepdf.Dictionary(XObject=pikepdf.Dictionary(Im0=xobj)),
                Contents=pikepdf.Stream(out, content),
            )
            out.pages.append(pikepdf.Page(pg))
            del img, bitmap
    except pdfium.PdfiumError as e:
        raise CleanError(f"Cannot render PDF: {e}") from e
    finally:
        doc.close()
    new_id = os.urandom(16)
    out.trailer["/ID"] = pikepdf.Array([pikepdf.String(new_id), pikepdf.String(new_id)])
    buf = io.BytesIO()
    out.save(buf, linearize=False, deterministic_id=False)
    out.close()
    return buf.getvalue()


# ---------------------------------------------------------------- independent checker

_ROOT_OK = {"/Type", "/Pages"}
_PAGE_OK = {"/Type", "/Parent", "/MediaBox", "/Resources", "/Contents"}
_OPS_OK = {"q", "Q", "cm", "Do"}
DIFF_LEVEL = 64          # a pixel "differs" when its grey value moves by more than this
INK_LEVEL = 64           # a pixel is "ink" when it is darker than white by more than this
MAX_DIFF_SHARE = 0.25    # differing pixels allowed, as a share of the original's ink pixels ...
NOISE_SHARE = 0.00005      # ... plus this share of all pixels (JPEG edge noise)


def _structure(new: bytes, sizes: list[tuple[float, float]]) -> None:
    try:
        pdf = pikepdf.open(io.BytesIO(new))
    except pikepdf.PdfError as e:
        raise CleanError(f"Flattened PDF is not readable: {e}") from e
    try:
        info = pdf.trailer.get("/Info")
        if info is not None and len(info.keys()) > 0:
            raise CleanError("Flattened PDF has Document Info.")
        if set(pdf.Root.keys()) - _ROOT_OK:
            raise CleanError(f"Flattened PDF catalog has extra entries: {sorted(set(pdf.Root.keys()) - _ROOT_OK)}")
        for obj in pdf.objects:
            if isinstance(obj, (pikepdf.Dictionary, pikepdf.Stream)):
                for k in ("/Metadata", "/Annots", "/AcroForm", "/Outlines", "/Names", "/OpenAction", "/AA",
                          "/PieceInfo", "/Thumb", "/StructTreeRoot", "/EmbeddedFiles"):
                    if k in obj:
                        raise CleanError(f"Flattened PDF still has {k}.")
        if len(pdf.pages) != len(sizes):
            raise CleanError(f"Page count differs ({len(sizes)} before, {len(pdf.pages)} after flattening).")
        for i, (page, (w, h)) in enumerate(zip(pdf.pages, sizes), start=1):
            if set(page.obj.keys()) - _PAGE_OK:
                raise CleanError(f"Page {i} has extra entries: {sorted(set(page.obj.keys()) - _PAGE_OK)}")
            box = [float(x) for x in page.obj.MediaBox]
            if abs((box[2] - box[0]) - w) > 0.5 or abs((box[3] - box[1]) - h) > 0.5:
                raise CleanError(f"Page {i} size differs after flattening.")
            res = page.obj.get("/Resources")
            if res is None or set(res.keys()) != {"/XObject"}:
                raise CleanError(f"Page {i} has resources other than images.")
            for name, x in res.XObject.items():
                if x.get("/Subtype") != pikepdf.Name.Image:
                    raise CleanError(f"Page {i} resource {name} is not an image.")
            for ins in pikepdf.parse_content_stream(page.obj):
                if str(ins.operator) not in _OPS_OK:
                    raise CleanError(f"Page {i} has the drawing operator '{ins.operator}' (only images allowed).")
    finally:
        pdf.close()


def _grey(page, scale: float):
    try:
        return page.render(scale=scale, fill_color=(255, 255, 255, 255), may_draw_forms=True).to_pil().convert("L")
    finally:
        page.close()


def check_flattened_pdf(original: bytes, new: bytes) -> None:
    pdfium = _pdfium()
    from PIL import ImageChops
    try:
        do, dn = pdfium.PdfDocument(original), pdfium.PdfDocument(new)
    except pdfium.PdfiumError as e:
        raise CleanError(f"Cannot render PDF for the check: {e}") from e
    try:
        sizes = []
        for i in range(len(do)):
            pg = do[i]
            sizes.append(pg.get_size())
            pg.close()
        _structure(new, sizes)
        if len(dn) != len(do):
            raise CleanError("Page count differs after flattening.")
        for i, (w, h) in enumerate(sizes):
            scale = min(DPI / 72.0, (MAX_PAGE_PIXELS / max(w * h, 1.0)) ** 0.5)
            a = _grey(do[i], scale)
            b = _grey(dn[i], scale)
            if b.size != a.size:
                b = b.resize(a.size)
            a, b = a.reduce(2), b.reduce(2)   # 2x2 averaging hides JPEG ringing at glyph edges, keeps the shapes
            total = a.width * a.height
            ink = sum(a.point(lambda v: 255 if v < 255 - INK_LEVEL else 0).histogram()[255:])
            diff = sum(ImageChops.difference(a, b).point(lambda v: 255 if v > DIFF_LEVEL else 0).histogram()[255:])
            if diff > MAX_DIFF_SHARE * ink + NOISE_SHARE * total:
                raise CleanError(f"Page {i + 1} of the flattened PDF does not look like the original "
                                 f"({diff} of {total} pixels differ, original has {ink} ink pixels).")
    except pdfium.PdfiumError as e:
        raise CleanError(f"Cannot render PDF for the check: {e}") from e
    finally:
        do.close()
        dn.close()
