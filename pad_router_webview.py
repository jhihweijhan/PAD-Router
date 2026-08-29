#!/usr/bin/env python3
"""Offline pywebview shell for the incremental PAD Router workspace."""

from __future__ import annotations

from pathlib import Path

from pad_router_gui import BoardInspectionBridge


ASSET_ROOT = Path(__file__).resolve().parent / "webview"


def main() -> None:
    try:
        import webview
    except ModuleNotFoundError as exc:
        raise SystemExit(
            "開啟 webview 介面需要 pywebview[gtk]；請先依 README 安裝相依套件"
        ) from exc

    index = ASSET_ROOT / "index.html"
    if not index.is_file():
        raise SystemExit(f"找不到 webview 本機資產：{index}")

    bridge = BoardInspectionBridge()
    window = webview.create_window(
        "PAD Router — 裝置工作區",
        url=index.as_uri(),
        js_api=bridge,
        width=1280,
        height=820,
        min_size=(960, 640),
        resizable=True,
        confirm_close=True,
    )
    window.events.closed += bridge.close
    try:
        webview.start(gui="gtk")
    finally:
        bridge.close()


if __name__ == "__main__":
    main()
