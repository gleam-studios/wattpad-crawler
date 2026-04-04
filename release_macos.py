#!/usr/bin/env python3

import argparse
import re
import subprocess
import sys
from pathlib import Path

from package_app import APP_NAME, build


def run(cmd: list[str], check: bool = True, capture_output: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(
        cmd,
        check=check,
        text=True,
        capture_output=capture_output,
    )


def list_code_signing_identities() -> list[str]:
    result = run(
        ["security", "find-identity", "-v", "-p", "codesigning"],
        capture_output=True,
        check=False,
    )
    identities: list[str] = []
    for line in result.stdout.splitlines():
        match = re.search(r'"([^"]+)"', line)
        if match:
            identities.append(match.group(1))
    return identities


def find_developer_id_application_identity(explicit_identity: str | None = None) -> str:
    identities = list_code_signing_identities()
    if explicit_identity:
        if explicit_identity not in identities:
            raise RuntimeError(
                f"Requested signing identity not found: {explicit_identity}\nAvailable identities: {identities or 'none'}"
            )
        return explicit_identity

    for identity in identities:
        if identity.startswith("Developer ID Application:"):
            return identity

    raise RuntimeError(
        "No 'Developer ID Application' certificate found in Keychain. "
        "Current machine only supports development signing until that certificate is installed."
    )


def has_notary_profile(profile: str) -> bool:
    result = run(
        ["xcrun", "notarytool", "history", "--keychain-profile", profile],
        capture_output=True,
        check=False,
    )
    return result.returncode == 0


def sign_app_bundle(app_path: Path, identity: str) -> None:
    cmd = [
        "codesign",
        "--force",
        "--deep",
        "--sign",
        identity,
        "--options",
        "runtime",
        "--timestamp",
        str(app_path),
    ]
    run(cmd)
    run(["codesign", "--verify", "--deep", "--strict", "--verbose=2", str(app_path)])


def zip_app(app_path: Path, zip_path: Path) -> None:
    if zip_path.exists():
        zip_path.unlink()
    run(
        [
            "ditto",
            "-c",
            "-k",
            "--sequesterRsrc",
            "--keepParent",
            str(app_path),
            str(zip_path),
        ]
    )


def notarize(zip_path: Path, profile: str) -> None:
    run(
        [
            "xcrun",
            "notarytool",
            "submit",
            str(zip_path),
            "--keychain-profile",
            profile,
            "--wait",
        ]
    )


def staple(app_path: Path) -> None:
    run(["xcrun", "stapler", "staple", "-v", str(app_path)])
    run(["xcrun", "stapler", "validate", "-v", str(app_path)])


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Build, Developer-ID sign, notarize, and staple the WattpadTool macOS app."
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="Only print signing/notarization readiness and exit.",
    )
    parser.add_argument(
        "--identity",
        help="Exact Developer ID Application identity name. If omitted, the first matching identity is used.",
    )
    parser.add_argument(
        "--notary-profile",
        default="WattpadToolNotary",
        help="Keychain profile name created via 'xcrun notarytool store-credentials'.",
    )
    parser.add_argument(
        "--skip-build",
        action="store_true",
        help="Use the existing app in dist/ instead of rebuilding with PyInstaller first.",
    )
    parser.add_argument(
        "--debug-console",
        action="store_true",
        help="Pass through to the build step when not skipping build.",
    )
    parser.add_argument(
        "--skip-notarize",
        action="store_true",
        help="Sign only. Useful while testing Developer ID signing before notary credentials are configured.",
    )
    args = parser.parse_args()

    if sys.platform != "darwin":
        raise SystemExit("release_macos.py only works on macOS.")

    root = Path(__file__).resolve().parent
    app_path = root / "dist" / f"{APP_NAME}.app"
    signed_zip = root / "dist" / f"{APP_NAME}-signed.zip"
    notarized_zip = root / "dist" / f"{APP_NAME}-notarized.zip"

    identities = list_code_signing_identities()
    developer_identities = [item for item in identities if item.startswith("Developer ID Application:")]
    profile_exists = has_notary_profile(args.notary_profile)

    if args.check:
        print("macOS release readiness")
        print(f"- Developer ID Application identities: {developer_identities or 'none'}")
        print(f"- Requested notary profile '{args.notary_profile}': {'found' if profile_exists else 'missing'}")
        print(f"- Existing app bundle: {'found' if app_path.exists() else 'missing'}")
        return 0 if developer_identities and profile_exists else 1

    identity = find_developer_id_application_identity(args.identity)
    print(f"Using signing identity: {identity}")

    if not args.skip_build:
        app_path = build(debug_console=args.debug_console)
        print(f"Built app: {app_path}")
    elif not app_path.exists():
        raise RuntimeError(f"Expected existing app bundle at: {app_path}")

    sign_app_bundle(app_path, identity)
    print(f"Signed app: {app_path}")

    zip_app(app_path, signed_zip)
    print(f"Signed zip: {signed_zip}")

    if args.skip_notarize:
        print("Skipped notarization by request.")
        return 0

    if not profile_exists:
        raise RuntimeError(
            "Notary credentials are not configured for profile "
            f"'{args.notary_profile}'. Run:\n"
            f"xcrun notarytool store-credentials {args.notary_profile}\n"
            "and provide your Apple notarization credentials."
        )

    notarize(signed_zip, args.notary_profile)
    print("Notarization finished.")

    staple(app_path)
    print("Stapled and validated app ticket.")

    zip_app(app_path, notarized_zip)
    print(f"Notarized zip: {notarized_zip}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
