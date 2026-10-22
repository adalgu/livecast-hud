# Copyright 2026 Kunwoo Kim
# SPDX-License-Identifier: Apache-2.0
"""Regenerate docs/screenshot.png from the example talk, offscreen (no window is shown, no network).

    QT_QPA_PLATFORM=offscreen PYTHONPATH=src python3 scripts/make_screenshot.py [output.png]
"""
import os
import sys
from pathlib import Path

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from livecast_hud.config import Config  # noqa: E402
from livecast_hud.overlay import SubtitleOverlayWindow  # noqa: E402
from PyQt6.QtGui import QColor, QPainter, QPixmap  # noqa: E402
from PyQt6.QtWidgets import QApplication  # noqa: E402


def main(out: Path):
    app = QApplication.instance() or QApplication([])
    by_id = {s["id"]: s for s in Config.load(ROOT / "examples" / "talks.json").script(1)}
    w = SubtitleOverlayWindow()
    w.resize(1000, w.height())
    w.show()
    w.set_talk(1)
    for sid in ("s03", "s04"):                                   # two committed sentences
        w.set_subtitles(by_id[sid]["ko"], by_id[sid]["en"], is_final=True)
    speaking = by_id["s05"]                                      # the sentence in progress: draft English + Korean so far
    w.set_subtitles(" ".join(speaking["ko"].split()[:6]), "Our sharing day runs every Saturday from 2 to 5 p.m.", is_final=False)
    app.processEvents()
    hud = w.grab()
    pad = 24                                                     # a plain slide-like backdrop so the translucent box reads
    canvas = QPixmap(hud.width() + 2 * pad, hud.height() + 2 * pad)
    canvas.fill(QColor("#3a4150"))
    p = QPainter(canvas)
    p.drawPixmap(pad, pad, hud)
    p.end()
    if not canvas.save(str(out)):
        sys.exit(f"could not save {out}")
    print(f"saved {out} ({canvas.width()}x{canvas.height()})")


if __name__ == "__main__":
    main(Path(sys.argv[1]) if len(sys.argv) > 1 else ROOT / "docs" / "screenshot.png")
