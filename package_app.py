#!/usr/bin/env python3

import argparse
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import PyInstaller.__main__


APP_NAME = "WattpadTool"
MAC_LAUNCHER_NAME = "Wattpad 中文工具箱"


def build(debug_console: bool = False) -> Path:
    root = Path(__file__).resolve().parent
    dist_dir = root / "dist"
    build_dir = root / "build" / "pyinstaller"
    spec_dir = root / "build" / "spec"

    dist_dir.mkdir(parents=True, exist_ok=True)
    build_dir.mkdir(parents=True, exist_ok=True)
    spec_dir.mkdir(parents=True, exist_ok=True)

    args = [
        "--noconfirm",
        "--clean",
        "--name",
        APP_NAME,
        "--distpath",
        str(dist_dir),
        "--workpath",
        str(build_dir),
        "--specpath",
        str(spec_dir),
        "--paths",
        str(root),
        "--collect-submodules",
        "requests",
        "--collect-submodules",
        "bs4",
        "--collect-submodules",
        "docx",
        "--hidden-import",
        "tkinter",
    ]

    if debug_console:
        args.append("--console")
    else:
        args.append("--windowed")

    if sys.platform.startswith("win"):
        args.append("--onefile")
    if sys.platform == "darwin":
        args.extend(["--osx-bundle-identifier", "local.wattpad.tool"])

    args.append(str(root / "wattpad_app.py"))

    PyInstaller.__main__.run(args)

    if sys.platform == "darwin":
        return dist_dir / f"{APP_NAME}.app"
    if sys.platform.startswith("win"):
        return dist_dir / f"{APP_NAME}.exe"
    return dist_dir / APP_NAME


def create_macos_local_launcher(app_path: Path) -> Path | None:
    if sys.platform != "darwin" or not app_path.exists():
        return None

    launcher_path = app_path.parent / f"{MAC_LAUNCHER_NAME}.app"
    embedded_app_path = launcher_path / "Contents" / "Resources" / f"{APP_NAME}.app"

    if launcher_path.exists():
        shutil.rmtree(launcher_path)

    applescript = f"""
on run
    try
        set wrapperBundle to POSIX path of (path to me)
        set targetBinary to wrapperBundle & "Contents/Resources/{APP_NAME}.app/Contents/MacOS/{APP_NAME}"
        do shell script "if [ ! -x " & quoted form of targetBinary & " ]; then echo missing; exit 64; fi"
        do shell script "nohup " & quoted form of targetBinary & " >/tmp/wattpadtool-gui.log 2>&1 &"
    on error errMsg number errNum
        display dialog "启动失败：" & errMsg buttons {{"好"}} default button "好" with icon stop
    end try
end run
"""

    with tempfile.NamedTemporaryFile("w", suffix=".applescript", delete=False) as handle:
        handle.write(applescript)
        script_path = Path(handle.name)

    try:
        subprocess.run(["osacompile", "-o", str(launcher_path), str(script_path)], check=True)
    finally:
        script_path.unlink(missing_ok=True)

    embedded_app_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(app_path, embedded_app_path, dirs_exist_ok=True)
    subprocess.run(
        ["codesign", "--force", "--deep", "--sign", "-", "--timestamp=none", str(launcher_path)],
        check=False,
    )
    subprocess.run(["xattr", "-cr", str(launcher_path)], check=False)
    return launcher_path


def package_macos_app(app_path: Path) -> Path | None:
    if sys.platform != "darwin" or not app_path.exists():
        return None
    zip_path = app_path.parent / f"{app_path.stem}-mac.zip"
    if zip_path.exists():
        zip_path.unlink()
    subprocess.run(
        [
            "ditto",
            "-c",
            "-k",
            "--sequesterRsrc",
            "--keepParent",
            str(app_path),
            str(zip_path),
        ],
        check=True,
    )
    return zip_path


def main() -> int:
    parser = argparse.ArgumentParser(description="Build the Wattpad Tool desktop app with PyInstaller.")
    parser.add_argument("--debug-console", action="store_true", help="Keep a console attached to the desktop app.")
    parser.add_argument("--zip", action="store_true", help="Create a distributable zip on macOS after building.")
    args = parser.parse_args()

    artifact = build(debug_console=args.debug_console)
    print(f"Built: {artifact}")

    distributable = artifact
    if sys.platform == "darwin":
        launcher = create_macos_local_launcher(artifact)
        if launcher:
            distributable = launcher
            print(f"Launcher: {launcher}")

    if args.zip:
        zipped = package_macos_app(distributable)
        if zipped:
            print(f"Zipped: {zipped}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
