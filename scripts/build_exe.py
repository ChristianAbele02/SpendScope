"""Build SpendScope.exe, a single-file Windows app, with PyInstaller.

The exe is written to the project root, next to ``data/``, so it uses the same
database as ``python run.py``. Moved elsewhere, it creates its own ``data/``
folder next to itself (or uses ``SPENDSCOPE_DATA_DIR``).

Usage (PowerShell, project root, venv active)::

    pip install -e ".[build]"            # PyInstaller
    python scripts\\build_exe.py          # -> SpendScope.exe
    python scripts\\build_exe.py --console  # keep a console window for debugging

The icon is drawn at build time with Pillow, so no binary file lives in git.
"""
import argparse
import importlib.util
import logging
import os
import platform
import shutil
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
BUILD_DIR = PROJECT_ROOT / "build"
APP_NAME = "SpendScope"

# Folders shipped inside the exe: (source, destination inside the bundle).
DATA_FOLDERS = (
    ("app/templates", "app/templates"),
    ("app/static", "app/static"),
    ("migrations", "migrations"),
)
# Imported inside functions or loaded by Alembic at runtime, so PyInstaller's
# static analysis cannot see them.
HIDDEN_IMPORTS = (
    "logging.config",           # migrations/env.py
    "sqlalchemy.dialects.sqlite",
    "flask_migrate",
    "alembic.runtime.migration",
    "alembic.script",
    "qrcode",
    "qrcode.image.pil",
    "pytesseract",
    "scipy.special",
)
OPTIONAL_IMPORTS = ("anthropic", "webview")  # bundled when installed
# Large packages the app never imports; excluding them keeps the exe smaller.
EXCLUDES = ("tkinter", "matplotlib", "pandas", "pytest", "mypy", "IPython", "PyQt5", "PyQt6")

ICON_SIZES = (16, 24, 32, 48, 64, 128, 256)
ICON_BACKGROUND = (26, 29, 35, 255)    # matches the app's dark surface
ICON_BARS = ((0.30, (76, 175, 80, 255)), (0.55, (76, 175, 80, 255)), (0.85, (255, 193, 7, 255)))

logger = logging.getLogger("build_exe")


def draw_icon(path: Path) -> Path:
    """Draw the app icon (three rising bars on a rounded square) as a multi-size .ico."""
    from PIL import Image, ImageDraw

    size = max(ICON_SIZES)
    img = Image.new("RGBA", (size, size), (0, 0, 0, 0))
    draw = ImageDraw.Draw(img)
    draw.rounded_rectangle((0, 0, size - 1, size - 1), radius=size // 5, fill=ICON_BACKGROUND)
    margin, gap = size * 0.18, size * 0.06
    bar_width = (size - 2 * margin - 2 * gap) / len(ICON_BARS)
    bottom = size - margin
    for i, (height, colour) in enumerate(ICON_BARS):
        left = margin + i * (bar_width + gap)
        top = bottom - height * (size - 2 * margin)
        draw.rounded_rectangle((left, top, left + bar_width, bottom), radius=size // 32, fill=colour)
    path.parent.mkdir(parents=True, exist_ok=True)
    img.save(path, sizes=[(s, s) for s in ICON_SIZES])
    return path


def pyinstaller_args(icon: Path, console: bool) -> list[str]:
    """Command-line arguments for PyInstaller."""
    sep = os.pathsep  # ";" on Windows: source;destination
    args = [
        str(PROJECT_ROOT / "run.py"),
        "--name", APP_NAME,
        "--onefile",
        "--noconfirm",
        "--clean",
        "--icon", str(icon),
        "--distpath", str(PROJECT_ROOT),
        "--workpath", str(BUILD_DIR / "work"),
        "--specpath", str(BUILD_DIR),
        "--paths", str(PROJECT_ROOT),
    ]
    args.append("--console" if console else "--windowed")
    for source, dest in DATA_FOLDERS:
        args += ["--add-data", f"{PROJECT_ROOT / source}{sep}{dest}"]
    hidden = list(HIDDEN_IMPORTS) + [m for m in OPTIONAL_IMPORTS if importlib.util.find_spec(m)]
    for module in hidden:
        args += ["--hidden-import", module]
    for module in EXCLUDES:
        args += ["--exclude-module", module]
    return args


def main() -> None:
    """Draw the icon, run PyInstaller and report where the exe is."""
    logging.basicConfig(level=logging.INFO, format="%(message)s")
    parser = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    parser.add_argument("--console", action="store_true", help="show a console window (debugging)")
    args = parser.parse_args()

    if sys.platform != "win32":
        logger.warning("SpendScope.exe is a Windows build; on %s you get a native binary.", sys.platform)
    try:
        import PyInstaller
        import PyInstaller.__main__
    except ImportError:
        logger.error('PyInstaller is missing: pip install -e ".[build]"')
        sys.exit(1)

    logger.info(
        "Python %s, PyInstaller %s, building %s.exe ...",
        platform.python_version(), PyInstaller.__version__, APP_NAME,
    )
    icon = draw_icon(BUILD_DIR / f"{APP_NAME.lower()}.ico")
    PyInstaller.__main__.run(pyinstaller_args(icon, args.console))

    exe = PROJECT_ROOT / f"{APP_NAME}.exe"
    if exe.exists():
        logger.info("Built %s (%.0f MB)", exe, exe.stat().st_size / 1024 / 1024)
    shutil.rmtree(BUILD_DIR / "work", ignore_errors=True)


if __name__ == "__main__":
    main()
