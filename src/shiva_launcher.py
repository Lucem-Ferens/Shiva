"""Entry point for the frozen (Nuitka) build.

`--selftest` runs encrypt/decrypt and metadata-cleaning roundtrips (JPEG and PDF through the worker subprocess).
`--clean-worker` makes this exe act as the separate-process cleaning helper (same user, memory-capped; not a security sandbox) (see shiva/metadata/runner.py)."""
import sys
import tempfile
from pathlib import Path


def _selftest() -> int:
    from shiva import jobs, steps
    rc = []
    with tempfile.TemporaryDirectory() as td:
        f = Path(td) / "t.bin"
        f.write_bytes(b"shiva selftest" * 1000)
        r1 = jobs.run_jobs([f], "pw", shred_original=True)
        r2 = jobs.run_jobs([Path(td) / "t.bin.age"], "pw", shred_original=False)
        ok = r1[0].ok and r2[0].ok and f.read_bytes() == b"shiva selftest" * 1000
        # cleaning: needs the worker subprocess, Pillow and pikepdf inside the frozen build
        from PIL import Image
        import pikepdf
        img = Path(td) / "p.jpg"
        exif = Image.Exif(); exif[0x010F] = "SelftestCam"
        Image.new("RGB", (8, 8), (1, 2, 3)).save(img, "JPEG", exif=exif.tobytes())
        pdf = Path(td) / "d.pdf"
        doc = pikepdf.new(); doc.add_blank_page(); doc.docinfo["/Author"] = "SelftestAuthor"; doc.save(pdf)
        rc = steps.run_steps([img, pdf], "", steps.StepOptions(clean=True, encrypt=False))
        cleaned_ok = (all(r.ok for r in rc) and b"SelftestCam" not in rc[0].created.read_bytes()
                      and b"SelftestAuthor" not in rc[1].created.read_bytes())
        ok = ok and cleaned_ok
        # PDF flatten (pypdfium2 + pdfium.dll) and an Office file
        import zipfile
        fl = Path(td) / "f.pdf"
        doc = pikepdf.new(); doc.add_blank_page(); doc.docinfo["/Author"] = "SelftestFlat"; doc.save(fl)
        docx = Path(td) / "o.docx"
        with zipfile.ZipFile(docx, "w") as z:
            z.writestr("[Content_Types].xml", '<?xml version="1.0"?><Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"><Default Extension="xml" ContentType="application/xml"/><Override PartName="/word/document.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/></Types>')
            z.writestr("_rels/.rels", '<?xml version="1.0"?><Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"><Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/></Relationships>')
            z.writestr("word/document.xml", '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body><w:p><w:r><w:t>x</w:t></w:r></w:p></w:body></w:document>')
        r3 = steps.run_steps([fl], "", steps.StepOptions(clean=True, encrypt=False, pdf_categories={str(fl): ["flatten"]}))
        r4 = steps.run_steps([docx], "", steps.StepOptions(clean=True, encrypt=False))
        rc = rc + r3 + r4
        ok = ok and all(r.ok for r in r3 + r4) and b"SelftestFlat" not in r3[0].created.read_bytes()
        # interface language: the compiled zh_TW catalog must be found in translations/ next to the exe
        from PySide6.QtCore import QCoreApplication
        from shiva import i18n
        import shiva.gui  # noqa: F401  (a GUI import that fails only in the frozen build must fail the selftest)
        _app = QCoreApplication.instance() or QCoreApplication([])
        lang_ok = i18n.install("zh_TW") == "zh_TW" and i18n.tr("Add files") == "加入檔案"
        i18n.uninstall()
        if not lang_ok:
            print("SELFTEST_FAIL: zh_TW translation catalog not loaded", flush=True)
        ok = ok and lang_ok
    print("SELFTEST_OK" if ok else f"SELFTEST_FAIL {r1} {r2} {rc}", flush=True)
    return 0 if ok else 1


if __name__ == "__main__":
    if "--clean-worker" in sys.argv[1:2]:
        from shiva.metadata.worker import main as worker_main
        sys.exit(worker_main())
    if "--selftest" in sys.argv:
        sys.exit(_selftest())
    from shiva.gui import main
    sys.exit(main())
