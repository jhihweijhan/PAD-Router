# PAD Router

PAD Router is a desktop GUI for inspecting a 6×5 Puzzle & Dragons board, checking configurable leader conditions, finding or evaluating drag routes, and safely executing a confirmed route through ADB.

## Requirements

- Python 3.10+
- [uv](https://docs.astral.sh/uv/)
- Tk support in Python for the GUI
- `adb` and a connected Android device only when capturing or executing a route

## Start with uv

```bash
uv run python pad_router.py --gui
```

The GUI can open a PNG screenshot or capture the selected Android device. It shows the source image and detected board, lets you calibrate or correct cells, configure and save Rule Profiles, manually draw a route, or search for a qualifying route.

A route can execute only after the board is confirmed, its Team Condition passes, and the user confirms the final route. Device execution is verified against the expected board after the gesture.

## Tests

```bash
uv run python -m unittest -v
uv run python pad_router.py --self-check
```

## Notes

- The initial GUI supports PNG input and a standard 6×5 board.
- A GUI-capable Python build is required. On Debian/Ubuntu, install the matching `python3-tk` system package if `tkinter` is unavailable.
- Run `adb devices` before using live capture or execution.
