# Shiva: what each function supports

Shiva is **not** one mandatory workflow. Each function works on its own; the combined pipeline
(clean -> encrypt -> verify -> shred) is only a convenience. Users can run any single function.

| Function | Standalone use | Needs the others? |
|---|---|---|
| Clean metadata | "Clean metadata" button | No |
| Encrypt / decrypt | "Encrypt" / "Decrypt" buttons | No |
| Shred | "Shred" button | No |
| Pipeline | tick the steps you want | Optional combination |

Status legend: **done** = in the current code, **later** = needs a bundled tool
or further work first, **no** = deliberately not supported (the file is refused or reported "not cleaned").

## Encryption (age 1.3.2, bundled)
Format-independent: age encrypts a byte stream, so any file type works.
| Input | Status |
|---|---|
| Any single file, any size (streamed) | done |
| Folder (becomes `NAME.tar.age`, restored into a new folder) | done |
| Decrypt `.age` file / `.tar.age` folder archive | done |
| Passphrase mode (scrypt) | done |
| Public-key recipients (`age1...`, key files) | later (needs key management UI) |
| Post-quantum recipients (`age1pq1`) | later |
Not supported: files you cannot read, cloud placeholders that are not downloaded, links/junctions inside folders
(refused with a reason).

## Shredding (best effort, never "secure deletion")
| Input | Status |
|---|---|
| Single file | done |
| Folder tree (junction-safe, all-or-nothing plan) | done |
| Warnings for SSD, network and cloud-sync locations | done |
Refused: hard-linked files, files with hidden NTFS streams (when shredding a folder), reparse points.
Refused locations (`shred.refusal_reason`, checked in the engine and shown before the run as "Not supported" with the reason;
the path as written and, when it goes through links, its resolved target are both checked): a drive root or share root;
the user's profile folder itself (files inside it are fine); the Windows folder and everything inside it; Program Files,
Program Files (x86), ProgramData and Users as folders (their contents are allowed); the folder Shiva runs from, everything
in it, and its parent folders. The confirmation window lists the full path of every item (shortened in the middle, full
path in the tooltip).
Always reported: SSD wear levelling, NTFS journal, shadow copies, cloud sync can keep old data.

## Metadata cleaning
Never copies an unsupported file through silently. Unsupported = refused or reported "not cleaned".
| Format | Status | Method |
|---|---|---|
| JPEG | done | lossless segment strip, pixel-equality check |
| PNG (incl. 16-bit, APNG) | done | chunk whitelist, pixel-equality check |
| PDF | done | pikepdf, user picks categories, content-stream and resource check; optional "flatten pages to images" (pypdfium2, 150 DPI JPEG pages, max 200 pages) removes covered text but makes text unsearchable and drops links, bookmarks and forms; checked by structure plus a rendering comparison |
| GIF, WEBP | done | block/chunk whitelist, all-frames pixel check; text extensions and trailing data count as content changes |
| MP3, FLAC, OGG, Opus | done | byte-level tag removal (ID3v1/v2, APEv2, Lyrics3, FLAC blocks, Vorbis comments), audio-payload hash check; M4A/MP4 not supported |
| DOCX, XLSX, PPTX, ODT/ODS/ODP | done | port of mat2 office logic, in memory; per-category selection dialog (unticked = copied unchanged); comments, tracked changes and glossary count as content changes and are unticked by default; theme and numbering always kept; signed or encrypted files refused; embedded images cleaned |
| ZIP | done | images, audio, nested zips and office files inside are cleaned (office files inside ignore the category choice and are cleaned with every category); other members: refuse (default), omit or keep via options (GUI checkboxes pending) |
| M4A/MP4/MOV/AVI/WAV/AIFF | later | needs bundled ffmpeg/ExifTool and a licence check of that binary |
| TIFF, BMP, HEIC, AVIF, JXL, RAW | later | needs bundled ExifTool |
| EPUB, SVG, HTML/CSS, torrent | later | port from mat2 |
| Encrypted/signed PDF, XFA-only PDF | no | refused |
| Anything else | no | reported "not cleaned" |
What cleaning does not cover (shown in the result): pixel-noise fingerprints, watermarks, visible text, file name, size,
and the new file's timestamps.
