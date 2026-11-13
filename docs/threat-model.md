# Threat model

What Shiva protects against, what it does not, and what it assumes. Shiva is an early version (before 1.0): this
document is a statement of intent and of known limits, not a guarantee, and it has not been audited by a third party.
Per-format details are in [`formats.md`](formats.md); user-facing limits are in the README under "Honest limits".

## Who and what we protect against

| Situation | Shiva's aim |
|---|---|
| You share a file and do not want hidden metadata (location, author, camera, revision history, comments) to travel with it | Remove the categories listed in `formats.md` and say what was removed, what was kept and what could not be checked |
| A file is stored or sent where others may read it (cloud folder, e-mail, USB stick, lost laptop) | Encrypt it with the official age tool to a password or to recipient keys, then check that the result opens before anything is destroyed |
| A file you no longer want sits on your drive | Overwrite and delete it, warn when the drive type makes that unreliable, and never call the result "secure deletion" |
| A file from an untrusted source is opened for cleaning | Parse it in a separate helper process with a memory limit and a time limit, so a broken or hostile file fails the helper, not the window |
| A tampered download or bundled program | Pin the bundled age binaries and the Python packages by version and SHA-256, check them when building, publish the SHA-256 of each release |

## Not protected against

- **An attacker who already controls your computer or your Windows account.** Malware, a keylogger, or another program
  running as you can read the password while age runs, read Shiva's memory (including an unlocked private key) and read or
  change any file you can. Shiva cannot detect or stop this.
- **Forensic recovery after shredding on SSDs, NTFS journals, shadow copies and cloud sync.** Overwriting a file does not reach
  copies the drive or the sync service keeps elsewhere. Full-disk encryption is the reliable protection. Shiva warns, and does
  not claim more.
- **Anything outside the file's metadata.** Visible content, watermarks, pixel-level fingerprints, file names, sizes and the new
  file's timestamps stay. Cleaning removes what Shiva knows about in the formats it supports; an unsupported format is not
  cleaned.
- **Traffic analysis and network adversaries.** Shiva makes no network connections of its own. It does not hide that you use it.
- **Who the file was encrypted to.** age files do not record recipients, and file names stay visible in the names of `.age`
  files. A file encrypted to an SSH public key carries a short tag of that key, so files made for the same SSH key can be
  matched with each other.
- **Future quantum computers and files encrypted to standard keys.** Files encrypted to a standard age key or an SSH key use
  classical public-key encryption: someone who records them today could read them if such a computer exists. Post-quantum keys
  (which Shiva creates by default) are meant to close this. A password file is symmetric; its strength is the password's strength.
- **A flaw in a parsing library** (Pillow, pikepdf, lxml and others) triggered by a crafted file. The helper process limits
  memory, time and starting other programs, but it is **not a sandbox**: it still has your Windows user's rights.

## Traces Shiva does not control

- A `.age` file shows its size and the number and type of its recipients.
- Cleaned-only and decrypted outputs are plaintext copies that Shiva writes and does not shred; remove them yourself when done.
- The page file, the hibernation file and crash dumps can hold a password, an unlocked key or file contents.
- Shiva can copy public keys and the results table (which lists file names) to the clipboard; clipboard history (Win+V) and
  clipboard sync can keep or upload them.
- Windows Defender and other security software may send file names, sizes and hashes to their vendors, and may upload files.
- The NTFS journal, recent-file lists, search indexes and thumbnail caches can keep file names after a file is gone.

## Trust boundaries

1. **Shiva window** (Python, PySide6): holds the password and any unlocked key in memory until the run ends and, for a key, until
   the window is closed or rebuilt; Python cannot wipe it. Destructive options are never remembered between runs; the interface
   language is kept in a small settings file next to the program.
2. **Cleaning helper process**: reads one file, returns a cleaned copy and a report. Stopped on cancel or timeout and
   killed with Shiva (a job object with a memory limit). Same user rights as the window: it can read your files, use the network and start
   programs. Its answer is treated as untrusted (size-limited, checked, shown as plain text).
3. **age (official binary, separate process)**: receives the password only through its environment, a private key only through
   a pipe. Shiva never puts a secret on a command line and writes no key to disk except the key file you ask it to create.
4. **Shredding**: runs inside Shiva's own process, one held file at a time; refuses dangerous targets.
5. **Files on disk**: outputs are written next to the original or into the folder you chose and never overwrite an existing file.

## Assumptions

- Windows 10 or 11, a normal (non-administrator) account, and a computer that is not already compromised.
- The bundled age and the Python packages are what their upstream projects published. The hashes are checked when the release is
  built, not when you run Shiva, and a change at the source before the list was made would not be caught.
- You keep key files and their passwords safe. A lost key file cannot be recovered.

## Supply chain

The bundled age binaries and the Python packages are pinned (`third_party/MANIFEST.toml`, `requirements/lock.txt` with hashes),
the build refuses a different package set, and each release lists the SHA-256 of its zip. The Python interpreter and the C
compiler used to build are not hash-pinned. The zip is not code-signed; Windows may show a SmartScreen warning on first run.

## Reporting

Security problems: see [`SECURITY.md`](../SECURITY.md).
