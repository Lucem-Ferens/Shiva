"""Traditional Chinese (Taiwan) catalog: English source text -> translation. Basic version for testing the interface.

Conventions
- One key = one line or one sentence (the engine in shiva/i18n.py splits text into lines and sentences).
- %1, %2 ... are placeholders filled after translation; %T1 also translates the captured text (lists allowed).
- %U stands for a count such as "3 files" (the unit:* entries below give the noun); keys may contain it.
- Terms: 清理=Clean, 加密=Encrypt, 解密=Decrypt, 銷毀=Shred (覆寫並刪除), 密碼=passphrase, 中繼資料=metadata.
- Never "安全刪除": shredding is best-effort (盡力而為).
Engine strings are imported from their modules wherever possible so the keys cannot drift."""
from __future__ import annotations

import re

from shiva import steps
from shiva.metadata import office, pdf

_SENT = re.compile(r"(?<=[.!?])\s+")
CATALOG: dict[str, str] = {}


def _add(en: str, zh: str) -> None:
    if en in CATALOG and CATALOG[en] != zh:
        raise ValueError(f"duplicate catalog key with a different translation: {en!r}")
    CATALOG[en] = zh


def _s(text: str, *zh: str) -> None:
    """A multi-sentence English text: one translation per sentence, in order."""
    parts = _SENT.split(text)
    if len(parts) != len(zh):
        raise ValueError(f"{len(parts)} sentences but {len(zh)} translations: {text[:60]!r}")
    for en, z in zip(parts, zh):
        _add(en, z)


def _many(pairs: dict[str, str]) -> None:
    for en, zh in pairs.items():
        _add(en, zh)


# ------------------------------------------------------------------ counts
_many({
    "unit:file": "%1 個檔案", "unit:item": "%1 個項目", "unit:character": "%1 個字元", "unit:original": "%1 個原始檔",
})

# ------------------------------------------------------------------ window, pages, controls (gui.py)
_many({
    "Clean metadata": "清理中繼資料", "Images, PDF, Office, audio, ZIP": "圖片、PDF、Office、音訊、ZIP",
    "Encrypt": "加密", "Files and folders": "檔案與資料夾",
    "Shred": "銷毀", "Overwrite and delete": "覆寫並刪除",
    "Clean": "清理", "Decrypt": "解密", "Shred originals": "銷毀原始檔",
    "Encrypt the cleaned copies": "加密清理後的副本",
    "Shred the originals after a verified encryption": "加密並驗證無誤後，銷毀原始檔",
    "Ready": "就緒",
    "Encrypt files": "加密檔案", "Decrypt .age files": "解密 .age 檔案",
    "&Add files...": "加入檔案(&A)...", "Add files": "加入檔案", "Add folder...": "加入資料夾...",
    "Add folder": "加入資料夾", "Clear list": "清除清單", "Remove selected": "移除所選", "Show technical details": "顯示技術細節", "Old data may stay on the drive.": "舊資料可能留在磁碟上。", "More": "更多", "Language": "語言", "System default": "系統預設", "Not available yet.": "尚未提供。", "Reset saved choices": "重設已儲存的選擇", "About Shiva": "關於 Shiva", "Saved choices were reset.": "已重設儲存的選擇。", "Saved choices could not be reset.": "無法重設儲存的選擇。", "Clean file metadata, encrypt and decrypt with age, and shred files.": "清理檔案中繼資料、用 age 加密與解密，以及銷毀檔案。", "Free software, licence GPL-3.0-or-later.": "自由軟體，授權為 GPL-3.0-or-later。", "Warning:": "警告：", "Failed:": "失敗：", "Some items may leave traces.": "部分項目可能留下痕跡。", "Result of the run": "執行結果", "Drop files to add them": "拖放檔案即可加入",
    "Folders can't be cleaned yet.": "目前還不能清理資料夾。",
    "Add files to clean": "加入要清理的檔案",
    "Drop files here.": "把檔案拖放到這裡。",
    "Language, help and about": "語言、說明與關於",
    "Images, PDF, Office/ODF documents, audio and ZIP are supported.": "支援圖片、PDF、Office/ODF 文件、音訊與 ZIP。",
    "Cleaned copies are saved next to the originals.": "清理後的副本會存放在原始檔旁邊。",
    "Add files or folders to encrypt": "加入要加密的檔案或資料夾",
    "Drop anything here. To open .age files, choose Decrypt above.": "可以把任何東西拖放到這裡。要開啟 .age 檔案，選上方的「解密」。",
    "Add files or folders to shred": "加入要銷毀的檔案或資料夾",
    "Items are overwritten and deleted. This can't be undone.": "項目會先覆寫再刪除，無法復原。",
    "Add .age files to decrypt": "加入要解密的 .age 檔案",
    "Drop files ending in .age here.": "把副檔名為 .age 的檔案拖放到這裡。",
    "You need the passphrase they were encrypted with.": "需要加密時使用的密碼。",
    "Status": "狀態", "Name": "名稱", "Type": "類型", "Size": "大小", "Note": "備註", "Result": "結果",
    "Files in this list: status, name, type, size, note": "清單中的檔案：狀態、名稱、類型、大小、備註",
    "Results: status, name, result, size": "結果：狀態、名稱、結果、大小",
    "Details of the selected result": "所選結果的詳細資料", "Summary of the run": "執行摘要",
    "Show in folder": "在資料夾中顯示", "Copy results": "複製結果", "Results copied.": "已複製結果。",
    "Show more options": "顯示更多選項",
    "Optional next steps": "選用的後續步驟",
    "Tick \"Encrypt the cleaned copies\" first.": "先勾選「加密清理後的副本」。",
    "Available when you encrypt.": "加密時才能使用。",
    "JPEG: keep the rotation tag (only rotation is kept)": "JPEG：保留旋轉標記（只保留旋轉資訊）",
    "Keep the colour profile": "保留色彩描述檔",
    "padding, compression tables and hidden bytes in the image data": "影像資料中的填充、壓縮表與隱藏的位元組",
    "JPEG re-encoded (lossy)": "JPEG 已重新編碼（有損）",
    "WebP re-encoded (lossy)": "WebP 已重新編碼（有損）",
    "hidden bytes in the image data": "影像資料內的隱藏位元組",
    "image rotated to match its Exif orientation": "圖片已依 Exif 旋轉方向轉正",
    "Changed: %T1": "已變更：%1",
    "JPEG and WebP: keep the image data as it is (no re-encoding)": "JPEG 與 WebP：保留影像資料原樣（不重新編碼）",
    "JPEG: reduce colour detail (smaller files)": "JPEG：降低色彩細節（檔案較小）",
    "Warning: hidden bytes and compression tables in the image data stay.": "警告：影像資料中隱藏的位元組與壓縮表會保留。",
    "Learn more": "了解更多",
    "The picture itself is not touched, but the image data is copied as it is. It can "
    "still hold leftover pixels beyond the image edge (for example from cropping), "
    "compression tables that can identify the camera or program, and extra bytes "
    "inside the image data. Data hidden in the pixels themselves stays too.":
        "圖片本身不會被更動，但影像資料會原樣複製，因此可能仍留有超出圖片邊緣的殘餘像素（例如裁切後留下的）、"
        "可辨識相機或程式的壓縮表，以及影像資料內多餘的位元組。藏在像素本身的資料也會保留。",
    "By default, full colour detail is kept if the original had it. This option "
    "saves less colour detail instead; files get smaller and thin coloured lines "
    "may blur.":
        "預設會在原始檔具有完整色彩細節時保留。此選項改存較少的色彩細節，檔案較小，但細的彩色線條可能變模糊。",
    "Archives: leave out parts that can't be cleaned (they are missing from the copy)": "壓縮檔：排除無法清理的部分（副本中不會有這些部分）",
    "Archives: keep parts that cannot be cleaned (may leak metadata)": "壓縮檔：保留無法清理的部分（可能洩漏中繼資料）",
    "Encrypt files that can't be cleaned (their metadata stays)": "加密無法清理的檔案（其中繼資料會保留）",
    "Save results in another folder": "將結果存到其他資料夾", "Folder for the results": "存放結果的資料夾",
    "Output folder": "輸出資料夾", "Choose folder...": "選擇資料夾...", "Choose output folder": "選擇輸出資料夾",
    "Output folder note": "輸出資料夾說明",
    "Choose the folder for the results": "選擇存放結果的資料夾",
    "Choose the folder for the results.": "選擇存放結果的資料夾。",
    "The output folder does not exist.": "輸出資料夾不存在。",
    "The output folder is inside a folder in the list. Choose another folder.": "輸出資料夾位於清單中的某個資料夾內。改選其他資料夾。",
    "Passphrase": "密碼", "Sho&w passphrase": "顯示密碼(&W)", "Show passphrase": "顯示密碼",
    "Passphrase warning": "密碼警告", "Passphrase length": "密碼長度",
    "Repeat passphrase": "再次輸入密碼", "Passphrase match": "兩次密碼是否相符",
    "Why the button is disabled": "按鈕無法使用的原因", "&Cancel": "取消(&C)", "Cancel": "取消",
    "Cancel the run": "取消這次執行", "Back to list": "回到清單",
    "Items affected": "受影響的項目", "Why overwriting may not work here:": "覆寫在這裡可能無效的原因：",
    "(folder)": "(資料夾)", "Folder": "資料夾", "File": "檔案", "Counting...": "計算中...", "%N1 items": "%1 個項目",
    "✓ Done": "✓ 完成", "⚠ Warning": "⚠ 警告", "✗ Failed": "✗ 失敗", "– Skipped": "– 已略過",
    "⊘ Cancelled": "⊘ 已取消", "✗ Not fully shredded": "✗ 未完全銷毀",
    "Processed": "已處理", "Working...": "處理中...", "Waiting": "等待中", "Finishing...": "即將完成...",
    "Working on %N1 of %N2: %3": "正在處理第 %1 個，共 %2 個：%3", "%N1 s": "%1 秒", "Stops after the current item. Finished items are kept.": "目前這一項完成後停止，已完成的項目會保留。",
    "Wait for the current run to finish.": "等目前的執行完成。", "Nothing to do.": "沒有可以執行的事。",
    "%N1 added to %T2": "已將 %1 個項目加入「%2」", "%U added": "已加入 %U", "%N1 already in the list": "%1 個已在清單中",
    "Scanning %1 ...": "正在掃描 %1 ...",
    "'%1' is a name Shiva uses while restoring folders; rename it before archiving this folder":
        "「%1」是 Shiva 還原資料夾時使用的名稱；先改名，再封存這個資料夾",
    "%1: cannot be scanned (%2); it will be reported as failed.": "%1：無法掃描（%2），會列為失敗。",
    "Cancelled. Nothing was changed.": "已取消，沒有任何變更。",
    "%T1 Nothing was changed.": "%1沒有任何變更。",
    "Cancelling after the current file...": "將在目前的檔案處理完後取消...",
    "Closing after the current file...": "將在目前的檔案處理完後關閉...",
    "Files that can't be cleaned are encrypted but their originals are never shredded.": "無法清理的檔案會被加密，但它們的原始檔絕不會被銷毀。",
    "%1 - not supported yet": "%1 - 目前尚不支援", "%1 (%N2 items)": "%1（%2 個項目）",
    "No result. Check what is left. See details.": "沒有結果。檢查剩下的內容。請見詳細資料。",
})

# ------------------------------------------------------------------ dialogs (gui.py)
_many({
    "Choose what to remove from %1": "選擇要從 %1 移除的內容",
    "Categories found: %1. Ticked ones will be removed.": "找到的類別：%1。已勾選的會被移除。",
    "(changes content)": "（會改變內容）",
    'Items marked "changes content" alter what the PDF does or holds. Limits:': "標示「會改變內容」的項目會改變 PDF 的功能或內容。限制：",
    'Items marked "changes content" alter what readers of the document see.': "標示「會改變內容」的項目會改變讀者看到的文件內容。",
    "Unticked items are copied unchanged.": "沒勾選的項目會原封不動地複製。",
    "Images inside the document are cleaned too.": "文件內的圖片也會一併清理。",
    "Use these choices for all other PDFs (%1)": "對所有其他 PDF 套用這些選擇（%1 個）",
    "Use these choices for all other documents (%1)": "對所有其他文件套用這些選擇（%1 個）",
    "Use these choices": "使用這些選擇",
    "Remove content from the document?": "要從文件中移除內容嗎？",
    "The cleaned copy will not contain the items below.": "清理後的副本不會包含下列項目。",
    "Readers will no longer see them.": "讀者將看不到它們。",
    "Cleaning itself does not change the original.": "清理本身不會更改原始檔。",
    "The cleaned copy will be a new PDF in which every page is a picture of the original page.":
        "清理後的副本會是一份新的 PDF，其中每一頁都是原始頁面的圖片。",
    "Only what is visible on each page is kept, as pixels: text under an opaque box is gone, but anything faintly visible (a semi-transparent highlight, pale text) stays in the picture.":
        "每一頁只保留看得見的內容，並以像素形式存在：不透明方塊底下的文字會消失，但隱約可見的東西（半透明的標示、淡色文字）仍會留在圖片裡。",
    "Remove them": "移除",
    "Flatten pages to images?": "要把頁面轉成圖片嗎？",
    "Flatten pages": "轉成圖片",
    "Text can no longer be selected, copied or searched": "文字將無法再選取、複製或搜尋",
    "Links, bookmarks, form fields, attachments and comments are lost (visible comments stay as part of the picture)":
        "連結、書籤、表單欄位、附件與註解都會消失（看得見的註解會留在圖片裡）",
    "The file is usually larger, and small text may look less sharp": "檔案通常會變大，小字可能比較不清晰",
    "All other items in this list are removed too": "這份清單中的其他項目也會一併移除",
    "Save readable copies here?": "要把可讀的副本存放在這裡嗎？",
    "Results will be saved in %1.": "結果會存放在 %1。",
    "Unlike the originals, these copies are not encrypted.": "和原始檔不同，這些副本沒有加密。",
    "Save here": "存放在這裡",
    "Keep parts that cannot be cleaned?": "要保留無法清理的部分嗎？",
    "Parts of an archive or document that Shiva cannot clean will be copied unchanged.": "壓縮檔或文件中 Shiva 無法清理的部分會原封不動地複製。",
    "They may still contain personal metadata.": "它們仍可能含有個人的中繼資料。",
    "Cleaned files that contain such parts are marked in the results as 'NOT fully cleaned'.": "含有這類部分的清理結果會在結果中標示為「未完全清理」。",
    "Keep parts and clean": "保留這些部分並清理",
    "Encrypt %U without cleaning?": "要在不清理的情況下加密 %U嗎？",
    "Shiva can't clean these, so their metadata stays inside the encrypted files.": "Shiva 無法清理這些檔案，所以它們的中繼資料會留在加密檔案裡面。",
    "Their originals will not be shredded.": "它們的原始檔不會被銷毀。",
    "Encrypt anyway": "仍要加密",
    "%1: will also remove %T2 (changes content).": "%1：也會移除 %2（會改變內容）。",
})

# ------------------------------------------------------------------ ui_model.py: row notes, statuses, buttons
_many({
    "Checking...": "檢查中...", "Not found": "找不到", "Not supported": "不支援", "Not readable": "無法讀取",
    "Can't be cleaned": "無法清理",
    "File or folder not found.": "找不到檔案或資料夾。",
    "Not an .age file.": "這不是 .age 檔案。", "Switch to Encrypt to encrypt it.": "切換到「加密」來加密它。",
    "Already encrypted.": "已經加密過了。", "Switch to Decrypt to open it.": "切換到「解密」來開啟它。",
    "Will be decrypted.": "會解密。", "Will be encrypted.": "會加密。",
    "Also inside a listed folder.": "同時也在清單中的資料夾內。",
    "Will be shredded.": "會銷毀。",
    "This is a whole drive.": "這是整個磁碟。", "Shred files or folders inside it instead.": "改為銷毀其中的檔案或資料夾。",
    "This is your user folder.": "這是您的使用者資料夾。",
    "This is a system folder.": "這是系統資料夾。", "Shred your own files instead.": "改為銷毀您自己的檔案。",
    "Shiva runs from this folder.": "Shiva 正從這個資料夾執行。", "Shred files somewhere else.": "改為銷毀其他位置的檔案。",
    "Not shredded: %M1 Nothing was changed.": "未銷毀：%1沒有任何變更。",
    "Windows would not list the hidden data streams of %R1 (error %N2).": "Windows 無法列出 %1 的隱藏資料流（錯誤 %2）。",
    "Encrypted. Use the Decrypt switch on the Encrypt page.": "已加密。到「加密」頁面使用「解密」切換。",
    "Online-only cloud file. Make it available offline first.": "僅存在雲端的檔案。先設為可離線使用。",
    "Can't be read (%T1).": "無法讀取（%1）。", "access denied": "存取被拒", "file error": "檔案錯誤",
    "Metadata stays if encrypted.": "加密後中繼資料仍會保留。",
    "Not supported yet.": "目前尚不支援。", "Not supported.": "不支援。",
    "Will be cleaned.": "會清理。", "Will be cleaned and encrypted.": "會清理並加密。",
    "Will be cleaned, then its original shredded.": "會清理，然後銷毀其原始檔。",
    "Will be encrypted, then its original shredded.": "會加密，然後銷毀其原始檔。",
    "Will be cleaned and encrypted, then its original shredded.": "會清理並加密，然後銷毀其原始檔。",
    "Cloud-synced folder: copies may stay online": "雲端同步資料夾：副本可能留在線上",
    "SSD: overwriting can't be guaranteed": "SSD：無法保證覆寫有效",
    "Removable drive: overwriting can't be guaranteed": "卸除式磁碟：無法保證覆寫有效",
    "Network drive: copies may stay on the server": "網路磁碟：副本可能留在伺服器上",
    "Drive type unknown": "磁碟類型不明",
    "File larger than %1 MB cannot be cleaned yet": "大於 %1 MB 的檔案目前還不能清理",
    # passphrase block
    "Enter a passphrase.": "輸入密碼。", "Repeat the passphrase.": "再輸入一次密碼。",
    "Length: %U": "長度：%U",
    "⚠ Short passphrase: use 8 or more characters": "⚠ 密碼太短：使用 8 個字元以上",
    "✓ Passphrases match": "✓ 兩次密碼相符", "✗ Passphrases do not match": "✗ 兩次密碼不相符",
    # button labels
    "Clean %U": "清理 %U", "Encrypt %U": "加密 %U", "Decrypt %U": "解密 %U",
    "Encrypt %U and decrypt %N1": "加密 %U，並解密 %1 個", "Encrypt and decrypt %U": "加密並解密 %U",
    "Shred %U...": "銷毀 %U...",
    "Clean and encrypt %U": "清理並加密 %U",
    "Encrypt and shred %U...": "加密並銷毀 %U...", "Clean, encrypt and shred %U...": "清理、加密並銷毀 %U...",
    # reasons
    "Add files to clean.": "加入要清理的檔案。", "Add files or folders.": "加入檔案或資料夾。",
    "Checking the added items...": "正在檢查加入的項目...",
    "Add at least one file that can be cleaned.": "至少加入一個可以清理的檔案。",
    # standing notes
    "Cleaning removes known metadata only.": "清理只會移除已知的中繼資料。",
    "Watermarks, visible content, the file name and its dates are not changed.": "浮水印、可見內容、檔名與檔案日期都不會被更改。",
    "Shredding overwrites items and deletes them.": "銷毀會覆寫項目並將其刪除。",
    "Results are best-effort: SSDs, cloud sync, Windows shadow copies and the file system journal can keep traces.":
        "結果只能盡力而為：SSD、雲端同步、Windows 陰影複本與檔案系統日誌都可能留下痕跡。",
    "Full-disk encryption is the reliable protection.": "全磁碟加密才是可靠的保護。",
    "If you lose this passphrase, your files cannot be opened.": "如果遺失這個密碼，您的檔案就無法開啟。",
    "It cannot be recovered.": "這個密碼無法找回。",
    "Store it somewhere safe.": "把它存放在安全的地方。",
    "Each result is encrypted and verified by trial decrypt before its original is shredded.":
        "每個結果都會先加密並以試解密驗證，之後才銷毀其原始檔。",
    "Each result is encrypted and verified by trial decrypt.": "每個結果都會加密並以試解密驗證。",
    "The cleaned copy is not encrypted.": "清理後的副本沒有加密。",
    # output folder notes
    "The output folder is cloud-synced (%1): readable copies will be uploaded and old versions may stay online.":
        "輸出資料夾會雲端同步（%1）：可讀的副本會被上傳，舊版本可能留在線上。",
    "The output folder is cloud-synced (%1): the encrypted files will be uploaded.":
        "輸出資料夾會雲端同步（%1）：加密檔案會被上傳。",
    "The output folder is on a network drive: the server and its backups may keep copies.":
        "輸出資料夾位於網路磁碟：伺服器及其備份可能保留副本。",
    "The output folder is on an SSD: readable copies you delete later cannot be reliably overwritten.":
        "輸出資料夾位於 SSD：您之後刪除的可讀副本無法被可靠地覆寫。",
    "The output folder is on a removable drive: it may be lost or read by others.":
        "輸出資料夾位於卸除式磁碟：它可能遺失，或被其他人讀取。",
    "Shiva could not tell what kind of drive the output folder is on.": "Shiva 無法判斷輸出資料夾位於哪一種磁碟。",
})

# ------------------------------------------------------------------ confirmation dialogs built by ui_model.shred_dialog_text
_many({
    "Shred %U?": "要銷毀 %U嗎？",
    "Shred %U after encrypting?": "加密後要銷毀 %U嗎？",
    "Shred %U after cleaning?": "清理後要銷毀 %U嗎？",
    "Shred": "銷毀",
    "These items will be overwritten and deleted.": "這些項目會先覆寫再刪除。",
    "Overwriting is best effort and can't be guaranteed.": "覆寫只能盡力而為，無法保證。",
    "This can't be undone.": "這無法復原。",
    "No copy is made and nothing is checked first.": "不會先建立副本，也不會先做任何檢查。",
    "After each file is cleaned, encrypted and verified by trial decrypt, its original is overwritten and deleted.":
        "每個檔案清理、加密並以試解密驗證之後，會先覆寫再刪除其原始檔。",
    "After each file is encrypted and verified by trial decrypt, its original is overwritten and deleted.":
        "每個檔案加密並以試解密驗證之後，會先覆寫再刪除其原始檔。",
    "If any check fails, the original is kept.": "只要有任何檢查失敗，就會保留原始檔。",
    "After each cleaned copy is saved and checked against the original, the original is overwritten and deleted.":
        "每個清理後的副本存檔並與原始檔核對之後，會先覆寫再刪除原始檔。",
    "If cleaning changes content in another way, that original is kept and the result says so.":
        "如果清理以其他方式改變了內容，該原始檔會被保留，結果中也會說明。",
    "JPEG and lossy WebP images, including those inside documents and archives, are saved again with a small loss of quality. Shiva keeps no copy of the original images.":
        "JPEG 與有損 WebP 圖片（包含文件與壓縮檔內的）會重新存檔，畫質會略有損失。Shiva 不會保留原始圖片的副本。",
    "Archives with parts left out lose those parts.": "有排除部分內容的壓縮檔會失去那些部分。",
    "Their originals are kept.": "會保留它們的原始檔。",
})

# ------------------------------------------------------------------ results
_many({
    "Done": "完成", "Warning": "警告", "Failed": "失敗", "Skipped": "已略過", "Cancelled": "已取消",
    "Not fully shredded": "未完全銷毀",
    "Not fully shredded.": "未完全銷毀。",
    "Verify by trial decrypt": "以試解密驗證", "Shred original": "銷毀原始檔", "Not started": "未開始",
    "Not shredded": "未銷毀", "Shredded (best effort)": "已銷毀（盡力而為）", "Not shredded, original kept": "未銷毀，已保留原始檔",
    "Done, some metadata not removed": "完成，部分中繼資料未移除", ":": "：", "Steps for %1": "「%1」的步驟",
    "Some items were already overwritten or deleted.": "有些項目已經被覆寫或刪除。",
    "Check what is left in %1.": "檢查 %1 中剩下的內容。",
    "Cancelled while shredding.": "銷毀途中已取消。",
    "Cancelled.": "已取消。", "Original unchanged.": "原始檔未更動。",
    "See details.": "請見詳細資料。",
    "The original may be partly overwritten.": "原始檔可能已被部分覆寫。",
    "Select the row for the reason.": "選取此列可查看原因。",
    "The default name already exists, so a number was added.": "預設檔名已存在，所以加上了編號。",
    "Not enough free space for the output.": "可用空間不足，無法寫入輸出。",
    "Free some space or choose another output folder.": "釋出空間，或改選其他輸出資料夾。",
    "The passphrase doesn't match this file, or the file is damaged.": "密碼與此檔案不符，或檔案已損毀。",
    "Check the passphrase and run again.": "檢查密碼後再執行一次。",
    "The .age file is unchanged.": ".age 檔案未更動。",
    "Shiva could not finish this item.": "Shiva 無法完成這個項目。",
    "Select the row for details.": "選取此列可查看詳細資料。",
    "Another program is using the file.": "檔案正被其他程式使用。",
    "Close that program and run again.": "關閉該程式後再執行一次。",
    "Windows denied access to the file or the output folder.": "Windows 拒絕存取檔案或輸出資料夾。",
    "Check the permissions or choose another output folder.": "檢查權限，或改選其他輸出資料夾。",
    "The output path is too long.": "輸出路徑太長。",
    "Readable temporary data may still be in %1.": "可讀的暫存資料可能仍在 %1。",
    "Shred or delete it yourself.": "自行銷毀或刪除。",
    "A temporary file with readable data was deleted without being overwritten.": "含可讀資料的暫存檔已刪除，但未覆寫。",
    "Still there: %R1": "仍存在：%1", "Deleted, not overwritten: %R1": "已刪除，未覆寫：%1",
    "A path inside this folder is too long to restore later.": "資料夾內有路徑太長，之後無法還原。",
    "Shorten the names inside the folder and run again.": "縮短資料夾內的名稱後再執行一次。",
    "A path inside this archive is too long for this output folder.": "封存檔內有路徑對這個輸出資料夾來說太長。",
    "Choose an output folder with a shorter path.": "改選路徑較短的輸出資料夾。",
    "Shorten the file name or choose an output folder with a shorter path.": "縮短檔名，或改選路徑較短的輸出資料夾。",
    "FAT or exFAT drive: Shiva checks the file less strictly before deleting it than on NTFS.":
        "FAT 或 exFAT 磁碟：Shiva 在刪除前對檔案的檢查比在 NTFS 上寬鬆。",
    "Shredding is not supported on this drive.": "此磁碟不支援銷毀。",
    "Turn off shredding, or use a file on a local NTFS, FAT or exFAT drive.": "關閉銷毀，或改用本機 NTFS、FAT 或 exFAT 磁碟上的檔案。",
    "Shiva shreds only files on local NTFS, FAT or exFAT drives.": "Shiva 只能銷毀位於本機 NTFS、FAT 或 exFAT 磁碟上的檔案。",
    "The item changed while Shiva was working.": "項目在 Shiva 處理期間已變更。",
    "Close any program that uses it and run again.": "關閉使用它的程式後再執行一次。",
    "Windows would not list this item's hidden data.":"Windows 無法列出這個項目的隱藏資料。",
    "This item has hidden data (NTFS data streams) that would not be encrypted, and shredding would destroy it.":
        "這個項目有隱藏資料（NTFS 資料流）不會被加密，銷毀時會一併被覆寫。",
    "Turn off shredding or remove the hidden data.": "關閉銷毀，或先移除隱藏資料。",
    "Windows would not apply the safety limits to the encryption helper, so it was stopped before it processed the file.":
        "Windows 未能對加密輔助程式套用安全限制，因此在它處理檔案前就已停止。",
    "Shiva left alone an item in %1 that is named like a leftover but is not one it can recognise.":
        "Shiva 沒有處理 %1 中一個名稱像殘留檔、但無法辨認是否為自己建立的項目。",
    "Check it, and shred or delete it yourself if you do not need it.": "檢查它；若不需要，自行銷毀或刪除。",
    "Left alone: %R1": "未處理：%1",
    "Check the item's permissions and run again.": "檢查這個項目的權限後再執行一次。",
    "The file changed during the run.": "檔案在執行期間被更動。",
    "Close any program that edits it and run again.": "關閉正在編輯它的程式後再執行一次。",
    "The trial decrypt did not match, so the encrypted copy was removed.": "試解密的結果不符，加密副本已移除。",
    "Run again.": "再執行一次。",
    "The output changed after it was verified.": "輸出在驗證後被更動。",
    "Delete it and run again.": "刪除它後再執行一次。",
    "The folder was decrypted but could not be renamed.": "資料夾已解密，但無法重新命名。",
    "The data is in the hidden folder named in the details.": "資料在詳細資料列出的隱藏資料夾內。",
    "Rename or move it.": "自行重新命名或移動它。",
    "Original may be partly overwritten.": "原始檔可能已被部分覆寫。",
    "The original may be partly overwritten. Check it before use. See details.": "原始檔可能已被部分覆寫。使用前先檢查。請見詳細資料。",
    "Check it before use.": "使用前先檢查。",
    "The output folder %1 already exists.": "輸出資料夾 %1 已存在。",
    "The output file %1 already exists.": "輸出檔案 %1 已存在。",
    "The output folder %1 already exists in %2.": "%2 中已有輸出資料夾 %1。",
    "The output file %1 already exists in %2.": "%2 中已有輸出檔案 %1。",
    "Rename or move it, or choose another output folder.": "重新命名或移走它，或改選其他輸出資料夾。",
    "Failed. Contains a part that cannot be cleaned. Original unchanged. Learn more":
        "失敗。內含無法清理的部分。原始檔未更動。了解更多",
    ("The archive holds a part whose format cannot be cleaned (named under Reason), so nothing was written. "
     "Under \"Show more options\" you can leave such parts out (they are missing from the copy) or keep them "
     "(they may leak metadata)."):
        "壓縮檔內有格式無法清理的部分（名稱見「原因」），因此沒有寫出任何檔案。在「顯示更多選項」中，可以選擇排除這些部分（複本中會缺少它們），或保留它們（可能洩漏中繼資料）。",
    "Shredded (best effort).": "已銷毀（盡力而為）。",
    "See details for limits at this location.": "此位置的限制請見詳細資料。",
    "Part of the original may remain.": "原始檔可能還有一部分殘留。",
    "Decrypted to %T1.": "已解密並存為 %1。",
    "The .age file is kept.": "已保留 .age 檔。",
    "Encrypted and verified.": "已加密並驗證。",
    "Saved as %T1.": "已存為 %1。",
    "%1 in folder %2": "%1（位於資料夾 %2）",
    "Original kept.": "已保留原始檔。",
    "Encrypted without cleaning (its metadata stays).": "未經清理就加密（其中繼資料會保留）。",
    "Cleaned, encrypted and verified.": "已清理、加密並驗證。",
    "Original shredded (best effort).": "已銷毀原始檔（盡力而為）。",
    "The original could not be fully deleted.": "原始檔無法完全刪除。",
    "Original kept (not shredded; see details).": "已保留原始檔（未銷毀；請見詳細資料）。",
    "Not removed: %T1.": "未移除：%1。",
    "Warning in details.": "警告請見詳細資料。",
    "Limits apply: see details.": "有已知限制：請見詳細資料。",
    "Nothing was processed.": "沒有處理任何項目。",
    "%N1 done": "完成 %1 個", "%N1 warnings": "警告 %1 個", "%N1 warning": "警告 %1 個", "%N1 not fully shredded": "未完全銷毀 %1 個",
    "%N1 failed": "失敗 %1 個", "%N1 skipped": "已略過 %1 個", "%N1 cancelled": "取消 %1 個",
    "Nothing was changed.": "沒有任何變更。",
    "Cancelled items were not touched.": "已取消的項目未被更動。",
    "Finished with a problem: not fully shredded.": "已結束，但有問題：未完全銷毀。",
    "Check what is left.": "檢查剩下的內容。",
    "Select a row to see why.": "選取一列可查看原因。",
    "Done.": "完成。",
    # detail lines
    "File: %R1": "檔案：%1", "Output: %R1": "輸出：%1", "Removed: %T1": "已移除：%1", "Not removed: %T1": "未移除：%1",
    "Note: %M1": "備註：%1", "Status: %T1": "狀態：%1", "Reason: %R1": "原因：%1",
    "Original shredded (best effort): yes": "原始檔已銷毀（盡力而為）：是",
    "Original shredded (best effort): not completely": "原始檔已銷毀（盡力而為）：未完全",
})

# ------------------------------------------------------------------ storage warnings and shred caveats (engine)
_s("This file is on an SSD: wear levelling means overwriting cannot be guaranteed. "
   "Full-disk encryption is the reliable protection.",
   "這個檔案位於 SSD：由於損耗平均（wear levelling）機制，無法保證覆寫有效。",
   "全磁碟加密才是可靠的保護。")
_add("This file is on a removable drive, which is often flash memory: overwriting cannot be guaranteed.",
     "這個檔案位於卸除式磁碟（通常是快閃記憶體）：無法保證覆寫有效。")
_add("This file is on a network drive: the server and its backups may keep copies that Shiva cannot reach.",
     "這個檔案位於網路磁碟：伺服器及其備份可能保留 Shiva 無法觸及的副本。")
_add("Shiva could not tell what kind of drive this is; do not rely on overwriting.",
     "Shiva 無法判斷這是哪一種磁碟；不要仰賴覆寫。")
_add("This file is in a cloud-synced folder (%1): copies and old versions may remain online and in the provider's recycle bin.",
     "這個檔案位於雲端同步資料夾（%1）：副本與舊版本可能留在線上及服務商的資源回收筒。")
_add("The file system keeps a journal, and Windows shadow copies may hold earlier versions.",
     "檔案系統會保留日誌，Windows 陰影複本也可能保存較早的版本。")
_add("The file system keeps a journal and may write copy-on-write, and Windows shadow copies may hold earlier versions.",
     "檔案系統會保留日誌並可能採用寫入時複製，Windows 陰影複本也可能保存較早的版本。")
_s("Overwrite is best-effort: wear levelling on flash storage (SSDs, USB sticks), file system journals, "
   "shadow copies and cloud sync may keep copies. Full-disk encryption is the reliable protection.",
   "覆寫只能盡力而為：快閃儲存（SSD、USB 隨身碟）的損耗平均、檔案系統日誌、陰影複本與雲端同步都可能保留副本。",
   "全磁碟加密才是可靠的保護。")
_many({
    "NOT cleaned: %T1.": "未清理：%1。",
    "Metadata, file name, size and times of this file are unchanged.": "這個檔案的中繼資料、檔名、大小與時間都沒有改變。",
    "The original was kept: files that were not cleaned are never shredded here.": "已保留原始檔：沒有清理過的檔案在這裡絕不會被銷毀。",
    "The original was kept. Cleaning changed its content: %T1.": "已保留原始檔。清理改變了其內容：%1。",
    "This file type cannot be cleaned yet": "目前還不能清理這種檔案類型",
    "Output folder is not writable": "輸出資料夾無法寫入",
    "The output folder does not exist: %1": "輸出資料夾不存在：%1",
    "Can't write to the output folder %1: %2": "無法寫入輸出資料夾 %1：%2",
})

# ------------------------------------------------------------------ cleaners: category labels, details, limits, warnings
for _k, _zh in {
    "info": "文件資訊", "xmp": "XMP 中繼資料", "embedded_files": "內嵌檔案", "javascript": "JavaScript 與動作",
    "xfa": "XFA 表單", "thumbnails": "縮圖", "pieceinfo": "PieceInfo（應用程式資料）",
    "annotation_authors": "註解的作者與日期", "form_values": "表單欄位的值", "bookmarks": "書籤（大綱）",
    "annotations": "註解與標記（備忘、螢光標示、印章、繪圖）", "links": "連結（可點選的區域）",
    "hidden_text": "隱藏文字（不可見的文字層）", "hidden_layers": "隱藏圖層（預設為關閉）",
    "navigation_data": "命名目的地與文章串", "doc_labels": "頁碼標籤、輸出意圖與網頁擷取資料",
    "structure_text": "無障礙文字與文件語言", "doc_id": "文件 ID", "flatten": "把頁面轉成圖片（可移除被遮蓋的文字）",
}.items():
    _add(pdf._LABELS[_k], _zh)

_many({
    "%N1 object(s) with /Metadata": "%1 個含 /Metadata 的物件",
    "%N1 in structure elements or threads": "%1 個位於結構元素或文章串中",
    "%N1 attachment source(s)": "%1 個附件來源",
    "%N1 script or action entry(ies)": "%1 個指令碼或動作項目",
    "XFA form data": "XFA 表單資料",
    "%N1 thumbnail(s)": "%1 張縮圖",
    "%N1 PieceInfo dictionary(ies)": "%1 個 PieceInfo 字典",
    "%N1 annotation(s) with author/date fields": "%1 個含作者/日期欄位的註解",
    "%N1 field(s) with values": "%1 個有值的欄位",
    "%N1 bookmark(s)": "%1 個書籤",
    "%N1 file attachment(s) are kept unless 'Embedded files' is selected": "%1 個檔案附件會保留，除非勾選「內嵌檔案」",
    "%N1 link(s)": "%1 個連結",
    "%N1 invisible text operator(s)": "%1 個不可見的文字運算子",
    "%N1 hidden layer(s): %2": "%1 個隱藏圖層：%2",
    "%N1 content span(s)": "%1 段內容",
    "%N1 layer(s) that are off on screen but print are kept": "%1 個螢幕上關閉但會列印的圖層會保留",
    "%N1 destination/thread source(s)": "%1 個目的地/文章串來源",
    "%N1 text entry(ies) (/Alt, /ActualText, /Lang)": "%1 個文字項目（/Alt、/ActualText、/Lang）",
    "%N1 text entry(ies) (/Alt, /ActualText, /Lang), %N2 inside page content": "%1 個文字項目（/Alt、/ActualText、/Lang），其中 %2 個在頁面內容裡",
    "trailer /ID": "檔案尾端的 /ID",
    "%N1 page(s) become images": "%1 頁會變成圖片",
    "possible covered content on %N1 page(s)": "%1 頁可能有被遮蓋的內容",
    "text becomes unselectable and unsearchable, links, bookmarks and forms are lost, the file is usually larger":
        "文字將無法選取與搜尋，連結、書籤與表單會消失，檔案通常會變大",
    "Warning: possible covered content on page %N1 (a filled rectangle is drawn over text).":
        "警告：第 %1 頁可能有被遮蓋的內容（文字上方畫了一個填色矩形）。",
    "Nothing was removed; covered text stays in the file.": "沒有移除任何東西；被遮蓋的文字仍留在檔案中。",
    "Warning: possible covered content on %N1 more page(s).": "警告：另有 %1 頁可能有被遮蓋的內容。",
    "form object without its own resources was left unchanged (page %N1)": "沒有自己資源的表單物件未被更動（第 %1 頁）",
    "form objects without their own resources were left unchanged (%N1 more pages)": "沒有自己資源的表單物件未被更動（另有 %1 頁）",
    "hidden layers were not removed: a form object without its own resources depends on them":
        "隱藏圖層沒有移除：有一個沒有自己資源的表單物件依賴它們",
    "Page %N1: content could not be parsed, so hidden text and layers on it were not removed.":
        "第 %1 頁：內容無法解析，所以該頁的隱藏文字與圖層未被移除。",
    "visible text may have moved (page %N1)": "可見文字可能移動了位置（第 %1 頁）",
    "visible text may have moved (%N1 more pages)": "可見文字可能移動了位置（另有 %1 頁）",
    "extra data after the PDF end (%%EOF)": "PDF 結尾之後的多餘資料（%%EOF）",
    "damaged PDF structure was repaired (some content may be lost)": "已修復損毀的 PDF 結構（部分內容可能遺失）",
    "Every page is now a single image; text, links, bookmarks and forms of the original are not in the copy.":
        "每一頁現在都是一張圖片；原始檔的文字、連結、書籤與表單不在這份副本裡。",
})
_add(pdf.IMAGES_WARNING, "PDF 內嵌的圖片不會被清理。")
_s(pdf.NOT_CLEANED_WARNING,
   "沒有清理，而且此處無法偵測：被其他可見物件壓在下面的隱藏內容（例如被黑色矩形或另一張圖片蓋住的文字或圖片：失敗的塗黑）、"
   "帶有自己中繼資料（Exif）的圖片、字型與字型子集，以及所有可見的文字。",
   "白色、極小或位於頁面之外的文字不會被當作隱藏文字。",
   "用可以選取文字的檢視器檢查結果。")
_s(pdf.ANNOT_TEXT_WARNING, "已保留註解與標記：它們的文字仍在檔案裡（要移除選「註解與標記」）。")
_s(pdf.FLATTEN_SUPERSEDES, "轉成圖片會用圖片取代每一頁並產生新檔案，所以其他勾選的項目也會一併移除。")
_s(pdf.LIMIT_HIDDEN_TEXT,
   "隱藏文字指以繪製模式 3（不可見）畫出的文字，例如 OCR 文字層，或位於已關閉圖層中的文字。",
   "白色、極小、被裁切或位於頁面之外的文字不會被偵測到。")
_s(pdf.LIMIT_COVERED,
   "被其他物件壓在下面的內容（例如被黑色矩形蓋住的文字：失敗的塗黑）不會被移除。",
   "只有簡單的情況才會出現警告，使用圖形狀態資源（gs）的頁面則完全不會警告。")
_s(pdf.LIMIT_IMAGES, "PDF 內的圖片保有自己的中繼資料（Exif），不會被清理。")
_s(pdf.LIMIT_ATTACHMENTS,
   "檔案附件只有勾選「內嵌檔案」才會移除，勾選「註解與標記」不會移除。",
   "移除「註解與標記」也會移除用填寫與簽署輸入的文字、文字方塊、印章與手繪簽名。")
_s(pdf.LIMIT_SIGNATURES, "已簽署的 PDF 會被拒絕，因為清理會使簽章失效。")
_s(pdf.LIMIT_LAYERS,
   "隱藏圖層只依預設的圖層設定判斷。",
   "螢幕上關閉但列印時開啟的圖層會保留。",
   "註解外觀、圖樣、Type 3 字型與柔邊遮色片內的圖層內容，以及沒有自己資源的共用表單物件內的圖層內容，都不會處理。")
_s(pdf.LIMIT_ACCESSIBILITY,
   "移除無障礙文字（替代文字、取代文字、語言）會改變螢幕閱讀器與搜尋能用到的內容；預設為關閉。")

# Office / ODF categories (label, detail) and always-kept rows
for _k, (_zl, _zd) in {
    "props": ("文件屬性", "作者、最後修改者、日期、標題、主旨、關鍵字"),
    "app": ("應用程式屬性", "公司、範本名稱、應用程式、統計資料"),
    "custom": ("自訂文件屬性", "使用者自訂的名稱/值欄位"),
    "thumbnail": ("縮圖", "第一頁的小型預覽圖"),
    "rsid": ("編輯工作階段識別碼", "把編輯內容連到工作階段的 rsid 編號"),
    "template": ("範本連結與檔案路徑", "範本與儲存檔案的路徑"),
    "printer": ("印表機設定", "印表機名稱與設定"),
    "people": ("作者清單", "編輯或加註者的姓名與帳號（保留註解時會自動保留）"),
    "customxml": ("自訂 XML 資料與標籤", "增益集與文件管理系統儲存的資料"),
    "label": ("敏感度標籤", "組織的分類標籤"),
    "cache": ("隱藏的快取與殘留檔案", "垃圾資料夾、版面快取、介面設定、內嵌物件的預覽圖"),
    "settings": ("檢視與網頁設定", "視窗、簡報與網頁發佈設定"),
    "comments": ("註解", "註解與回覆，含其作者"),
    "tracked": ("追蹤修訂（接受插入、移除刪除）", "誰改了什麼；文字會保留最新版本"),
    "glossary": ("詞彙庫（建置組塊）", "已儲存的建置組塊（自動圖文集、快速組件）"),
}.items():
    _lab, _det, _cc = office.CATEGORIES[_k]
    _add(_lab, _zl); _add(_det, _zd)
_add(office.C_PROPS, "文件屬性（作者、日期）")
_add(office.C_APP, "應用程式屬性（公司、範本、統計資料）")
_add(office.C_TRACK, "追蹤修訂")
_add(office.C_COMMENTS, "註解")
_add(office.C_RSID, "編輯工作階段識別碼（rsid）")
_add(office.C_TEMPLATE, "文件範本的連結（路徑）")
_add(office.C_PATH, "文件在本機的檔案路徑")
_add(office.C_PRINTER, "印表機設定")
_add(office.C_GLOSSARY, "詞彙庫（建置組塊）")
for _k, (_zl, _zd, _zr) in {
    "theme": ("佈景主題檔案", "文件設計的色彩、字型與效果", "一律保留：不含個人資料，移除可能改變文件外觀。"),
    "numbering": ("編號定義", "清單與標題的編號樣式", "一律保留：不含個人資料，移除可能破壞清單與編號。"),
}.items():
    _lab, _det, _why = office._ALWAYS_KEPT[_k]
    _add(_lab, _zl); _add(_det, _zd); _add(_why, _zr)
_add("Parts that cannot be cleaned", "無法清理的部分")
_s(office._BLOCKING_REASON,
   "Shiva 無法清理這些部分，所以整個檔案會被拒絕。",
   "要繼續，在「顯示更多選項」中勾選「壓縮檔：排除…」或「壓縮檔：保留…」。")
for _k, (_lab, _det, _cc) in office.CATEGORIES.items():
    if _cc:
        _add(f"{_lab} kept ({_det})", f"{CATALOG[_lab]}已保留（{CATALOG[_det]}）")
_many({
    "hidden trash folder": "隱藏的垃圾資料夾", "custom XML data": "自訂 XML 資料", "web settings": "網頁設定",
    "custom document properties": "自訂文件屬性", "thumbnail": "縮圖", "author list (people)": "作者清單",
    "custom tags": "自訂標籤", "view settings": "檢視設定", "presentation settings": "簡報設定",
    "relationship lists of removed parts": "已移除部分的關聯清單", "sensitivity label": "敏感度標籤",
    "object preview images": "物件預覽圖", "layout cache": "版面快取", "interface configuration": "介面設定",
    "file times and attributes stored in the zip": "壓縮檔內記錄的檔案時間與屬性",
    "file times, permissions and comments stored in the zip": "壓縮檔內記錄的檔案時間、權限與註解",
    "empty folders": "空資料夾", "embedded image metadata": "內嵌圖片的中繼資料",
    "embedded audio metadata": "內嵌音訊的中繼資料",
    "omitted member with an unsupported format: %1": "已排除格式不支援的成員：%1",
    "%1 was kept unchanged and may leak metadata": "%1 被原封不動地保留，可能洩漏中繼資料",
    steps.KEPT_UNCLEAN: "原封不動複製的部分（未完全清理）",
})
# image, audio and archive labels
_many({
    "other APPn": "其他 APPn 區段", "JFIF thumbnail": "JFIF 縮圖", "ICC profile": "ICC 色彩描述檔",
    "Exif GPS": "Exif GPS 位置", "Exif camera/date": "Exif 相機/日期資料", "XMP": "XMP", "Exif": "Exif",
    "IPTC/Photoshop": "IPTC/Photoshop 資料", "JPEG comment": "JPEG 註解", "Exif orientation": "Exif 旋轉方向",
    "data after EOI": "影像結尾（EOI）之後的多餘資料",
    "The Exif orientation tag (%N1) was removed; the image may display rotated.": "已移除 Exif 旋轉標記（%1）；影像顯示時可能會是旋轉的。",
    "PNG text chunks": "PNG 文字區塊", "Exif (eXIf)": "Exif (eXIf)", "timestamp (tIME)": "時間戳記 (tIME)",
    "physical size (pHYs)": "實體尺寸 (pHYs)", "colour profile (iCCP)": "色彩描述檔 (iCCP)",
    "other PNG chunks": "其他 PNG 區塊", "data after IEND": "IEND 之後的多餘資料",
    "colour profile (ICCP)": "色彩描述檔 (ICCP)", "other WebP chunks": "其他 WebP 區塊",
    "data after the RIFF end": "RIFF 結尾之後的多餘資料",
    "colour profile (ICC)": "色彩描述檔 (ICC)", "plain text extension": "純文字擴充區塊",
    "visible text (plain text extension) removed": "已移除可見文字（純文字擴充區塊）",
    "GIF comments": "GIF 註解", "other application extensions": "其他應用程式擴充區塊",
    "an unrecognised application extension was dropped": "已捨棄一個無法辨識的應用程式擴充區塊",
    "data after the GIF trailer": "GIF 結尾之後的多餘資料",
    "application blocks": "應用程式區塊", "Vorbis comments": "Vorbis 註解", "cue sheet": "提示表",
    "embedded cover picture": "內嵌封面圖片", "other FLAC blocks": "其他 FLAC 區塊",
    "Opus comments": "Opus 註解", "encoder vendor string": "編碼器廠商字串",
    "unknown data after the audio stream": "音訊串流之後的未知資料",
    "ID3v2 tag": "ID3v2 標籤", "ID3v1 tag": "ID3v1 標籤", "APEv2 tag": "APEv2 標籤", "Lyrics3 tag": "Lyrics3 標籤",
})

# ------------------------------------------------------------------ inline warnings (confirmations replaced, 2026-11-03)
_many({
    "Warning about the ticked items": "勾選項目的警告",
    "Warning: every page becomes a picture.": "警告：每一頁都會變成圖片。",
    "Text can't be selected or searched, and links, bookmarks, form fields and attachments are lost.":
        "文字將無法選取或搜尋，連結、書籤、表單欄位與附件都會消失。",
    "Warning: the cleaned copy will not contain %1.": "警告：清理後的副本不會包含 %1。",
    "Warning: parts that can't be cleaned are copied as they are and may still hold personal metadata.":
        "警告：無法清理的部分會原封不動地複製，可能仍含有個人的中繼資料。",
    "Warning: these files keep their metadata inside the encrypted copy, and their originals are not deleted.":
        "警告：這些檔案的中繼資料會留在加密後的副本裡，它們的原始檔也不會被刪除。",
})

# ------------------------------------------------------------------ shred caveat redesign (2026-11-03)
_many({
    # page line, links, dialog
    "Learn about shredding": "了解銷毀", "Learn why shredding can fail": "了解為什麼銷毀可能失敗",
    "Shredding is best effort: traces can remain.": "銷毀只能盡力而為：痕跡可能殘留。",
    "Shred %U after encrypting?": "加密後要銷毀 %U嗎？",
    "Shred %U after cleaning?": "清理後要銷毀 %U嗎？",
    "Shred originals": "銷毀原始檔",
    "They will be overwritten and deleted as far as the drive allows.": "它們會在磁碟允許的範圍內被覆寫並刪除。",
    "No copy is kept and you can't undo this.": "不會留下副本，也無法復原。",
    "After each encrypted copy passes a trial decrypt, its original is overwritten and deleted as far as the drive allows.":
        "每個加密副本通過試解密後，其原始檔會在磁碟允許的範圍內被覆寫並刪除。",
    "If a check fails, the original is kept.": "如果檢查失敗，就會保留原始檔。",
    "Each original is overwritten and deleted as far as the drive allows, once its copy passes a trial decrypt. A failed check keeps it. You can't undo this.":
        "每個原始檔會在磁碟允許的範圍內被覆寫並刪除，但只在其副本通過試解密之後。檢查失敗就會保留。這無法復原。",
    "Add files...": "加入檔案...",
    "Available in a moment.": "稍後即可使用。", "Close": "關閉",
    # per-item warnings
    "Warning: on an SSD, so old data may stay on the drive.": "警告：位於 SSD，舊資料可能留在磁碟上。",
    "Warning: on a removable drive, so old data may stay on it.": "警告：位於卸除式磁碟，舊資料可能留在上面。",
    "Warning: on a network drive, so the server may keep copies.": "警告：位於網路磁碟，伺服器可能保留副本。",
    "Warning: in a cloud folder, so copies may stay online.": "警告：位於雲端資料夾，副本可能留在線上。",
    "Warning: on a drive of unknown type, so old data may stay on it.": "警告：位於類型不明的磁碟，舊資料可能留在上面。",
    "Warning: compressed by Windows, so old data may stay on the drive.": "警告：已被 Windows 壓縮，舊資料可能留在磁碟上。",
    "Warning: a sparse file, so old data may stay on the drive.": "警告：這是稀疏檔案，舊資料可能留在磁碟上。",
    "Warning: encrypted by Windows (EFS), so old data may stay on the drive.":
        "警告：已被 Windows 加密（EFS），舊資料可能留在磁碟上。",
    "Warning: contains a file compressed by Windows, so old data may stay on the drive.":
        "警告：內含已被 Windows 壓縮的檔案，舊資料可能留在磁碟上。",
    "Warning: contains a sparse file, so old data may stay on the drive.": "警告：內含稀疏檔案，舊資料可能留在磁碟上。",
    "Warning: contains a file encrypted by Windows (EFS), so old data may stay on the drive.":
        "警告：內含已被 Windows 加密（EFS）的檔案，舊資料可能留在磁碟上。",
    "This file is compressed by Windows: overwriting may leave the old data in other places on the drive.":
        "這個檔案已被 Windows 壓縮：覆寫後，舊資料可能留在磁碟的其他位置。",
    "This file is a sparse file: overwriting may leave the old data in other places on the drive.":
        "這個檔案是稀疏檔案：覆寫後，舊資料可能留在磁碟的其他位置。",
    "This file is encrypted by Windows (EFS): overwriting may leave the old data in other places on the drive.":
        "這個檔案已被 Windows 加密（EFS）：覆寫後，舊資料可能留在磁碟的其他位置。",
    # About shredding
    "About shredding": "關於銷毀",
    "What Shred does": "銷毀做了什麼",
    "Shred overwrites the contents of an item, then deletes it.": "銷毀會覆寫項目的內容，然後刪除它。",
    "No copy is kept.": "不會留下副本。",
    "Why it can fail": "為什麼可能失敗",
    "Overwriting changes the place the drive was told to write to.": "覆寫改變的是磁碟被指示寫入的那個位置。",
    "Some storage keeps older data elsewhere.": "有些儲存裝置會把較舊的資料保留在別的地方。",
    "SSDs and USB sticks: wear levelling (spreading writes over the chip) can leave old data in place.":
        "SSD 與隨身碟：損耗平均（把寫入分散到整個晶片）可能讓舊資料留在原處。",
    "File system journal: Windows can keep a log of recent changes, and very small files can live in the file index.":
        "檔案系統日誌：Windows 可能保留最近變更的紀錄，很小的檔案也可能存放在檔案索引裡。",
    "Shadow copies and backups: restore points or backups can hold earlier versions of a file.":
        "陰影複本與備份：還原點或備份可能保存檔案較早的版本。",
    "Cloud folders and network drives: the provider or server can keep copies and old versions.":
        "雲端資料夾與網路磁碟：服務商或伺服器可能保留副本與舊版本。",
    "What helps": "有什麼幫助",
    "Turn on full-disk encryption before you store sensitive files.": "存放敏感檔案之前，先開啟全磁碟加密。",
    "Recovered data is then unreadable without your passphrase.": "這樣即使資料被還原，沒有您的密碼也無法讀取。",
    "Keep sensitive files in encrypted form (use the Encrypt page) instead of leaving readable copies.":
        "敏感檔案以加密形式保存（使用「加密」頁面），不要留下可讀的副本。",
    "Before you give away or sell a drive, overwrite the whole drive or destroy it.": "送人或賣掉磁碟之前，覆寫整顆磁碟或將它銷毀。",
    "What Shiva checks": "Shiva 會檢查什麼",
    "Shiva checks the drive type and cloud folders and shows a Warning on items that are worse than usual.":
        "Shiva 會檢查磁碟類型與雲端資料夾，並對風險比平常高的項目顯示警告。",
    "It cannot see shadow copies or backups.": "它看不到陰影複本或備份。",
    "What Shiva does not claim": "Shiva 不會宣稱什麼",
    "Shiva never promises that deleted data cannot be recovered.": "Shiva 不會保證已刪除的資料一定無法復原。",
    'Results are labelled "best effort".': "結果會標示為「盡力而為」。",
})
