# Third-party notices

Shiva is licensed under the GNU General Public License v3.0 or later (see `LICENSE.txt` in the portable
folder). The portable folder also contains the components below. Each keeps its own licence.

| Component | Version | Licence | Where it is in the folder | Source |
|---|---|---|---|---|
| age, age-keygen, age-inspect and plugins (batchpass, pq, tag, tagpq) | see `BUILD-INFO.txt` | BSD-3-Clause (`age/LICENSE`) | `age/*.exe`, unmodified official release binaries | https://github.com/FiloSottile/age |
| Qt 6 and Qt for Python (PySide6, Shiboken) | see `BUILD-INFO.txt` | LGPL-3.0 (text in `licenses/LGPL-3.0.txt`); Qt for Python is dual-licensed LGPL-3.0 / GPL-3.0 / commercial and Shiva uses it under the LGPL/GPL terms | `Qt6*.dll`, `PySide6/` and `shiboken6/` files next to `Shiva.exe` | https://code.qt.io/ and https://pypi.org/project/PySide6/ |
| CPython runtime, with the libraries it ships: OpenSSL (`libcrypto-3.dll`, `libssl-3.dll`), libffi, bzip2 and the Microsoft C runtime DLLs | see `BUILD-INFO.txt` | Python Software Foundation License; OpenSSL under the Apache License 2.0; the Microsoft DLLs under the terms in CPython's licence file; all texts are in `licenses/python/LICENSE.txt` | the Python runtime library (`python3*.dll`), the compiled standard library, `libcrypto-3.dll`, `libssl-3.dll`, `libffi-8.dll`, `msvcp140*.dll` | https://www.python.org/ |
| pypdfium2 (Python bindings, used only for the optional "Flatten pages to images" PDF cleaning) | see `BUILD-INFO.txt` | BSD-3-Clause or Apache-2.0, at the recipient's choice | `pypdfium2/`, `pypdfium2_raw/` | https://github.com/pypdfium2-team/pypdfium2 |
| PDFium (PDF renderer, prebuilt `pdfium.dll` shipped inside the pypdfium2 wheel) | bundled with pypdfium2 | BSD-3-Clause; it also contains third-party libraries under their own permissive licences (for example FreeType, libjpeg-turbo, zlib, OpenJPEG, Little CMS); the full list is in the licence files shipped in `licenses/pypdfium2/` (copied from the wheel at build time; they cover PDFium, abseil, AGG, fast_float, FreeType, ICU, Little CMS, libjpeg-turbo, OpenJPEG, libpng, llvm-libc, simdutf and zlib) | `pypdfium2_raw/pdfium.dll` | https://pdfium.googlesource.com/pdfium/ and https://github.com/bblanchon/pdfium-binaries |
| Pillow (image reading and rewriting for JPEG, PNG, GIF and WEBP cleaning) | see `BUILD-INFO.txt` | MIT-CMU (package metadata `License-Expression`); the binary wheel also contains compiled libraries, whose notices are in the licence files copied to `licenses/pillow/` | `PIL/` | https://python-pillow.org/ and https://github.com/python-pillow/Pillow |
| pikepdf (PDF cleaning) and the libraries its wheel bundles, including qpdf | see `BUILD-INFO.txt` | MPL-2.0 for pikepdf itself (package metadata `License-Expression`); the wheel vendors compiled third-party libraries, and the attribution file and licence texts are copied to `licenses/pikepdf/` | `pikepdf/`, `pikepdf.libs/` | https://github.com/pikepdf/pikepdf and https://github.com/qpdf/qpdf |
| lxml (pulled in by pikepdf) and the libraries its wheel bundles (libxml2, libxslt) | see `BUILD-INFO.txt` | BSD-3-Clause; the bundled libraries are under permissive licences listed in the files copied to `licenses/lxml/` | `lxml/` | https://lxml.de/ |
| Nuitka (build tool; its C runtime is linked into the executable) | see `BUILD-INFO.txt` | AGPL-3.0-or-later with the Nuitka Runtime Library Exception, which allows the runtime to be linked into a program under another licence; both texts are in `licenses/nuitka/` | `Shiva.exe` | https://github.com/Nuitka/Nuitka |

This software is based in part on the work of the Independent JPEG Group.

Passwords: the character sets of random-character passwords follow the password generator of Proton Pass
(https://github.com/protonpass/proton-pass-common, GPL-3.0-or-later). The passphrase word list is the "EFF Large Wordlist for
Passphrases" by the Electronic Frontier Foundation (Joseph Bonneau), https://www.eff.org/dice, licensed under the Creative
Commons Attribution 4.0 International License, https://creativecommons.org/licenses/by/4.0/. It is shipped unchanged, as its
own module inside the program (`shiva/wordlist.py`, whose header repeats this notice), and stays under that licence.

## Your rights under the GPL and LGPL

- Shiva's complete corresponding source code is the public repository this release was built from
  (the exact commit is named in the release notes). It includes `tools/build.py`, which produced this folder.
- The Qt libraries are shipped as separate, replaceable `.dll` files, so you can substitute a modified
  build of Qt as the LGPL requires.
- Code ported into Shiva from MAT2 (version 0.15.0, LGPL-3.0-or-later: the Office/ODF and ZIP cleaners in
  `src/shiva/metadata/office.py` and `zipclean.py`) keeps its notice in the header of each ported source file.
  Ported components are listed in `third_party/MANIFEST.toml` in the repository. No BleachBit code has been
  ported so far; if some is, it will be listed there the same way.

