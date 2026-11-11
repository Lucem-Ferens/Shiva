"""Shiva window: three pages (Clean metadata, Encrypt, Shred), each with its own file list.

Rules and wording live in `ui_model` (Qt-free); this package only builds widgets and runs jobs.
"""
from .dialogs import AboutShreddingDialog, ConfirmDialog, PdfDialog
from .page import TabPage, _EMPTY, _Row
from .widgets import NavButton, apply_theme, high_contrast_active
from .window import MainWindow, _KEEP, _OFFICE_FMTS, _ProbeBridge, _Worker, main

__all__ = ["AboutShreddingDialog", "ConfirmDialog", "PdfDialog", "TabPage", "NavButton", "MainWindow",
           "apply_theme", "high_contrast_active", "main"]
