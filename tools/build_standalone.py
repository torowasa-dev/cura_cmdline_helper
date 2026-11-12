#!/usr/bin/env python3
"""Embed the reviewable Cura bridge into the standalone launcher.

Uses only the Python standard library. Never starts Cura or any native engine.
ZIP member order, metadata and timestamps are fixed for reproducible output.
"""
from __future__ import annotations

import argparse
import ast
import base64
import io
from pathlib import Path
import sys
import zipfile

ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = ROOT / "src" / "cura_reslice.py"
BRIDGE = ROOT / "src" / "HeadlessResliceBridge"
OUTPUT = ROOT / "cura_reslice.py"
MEMBERS = ("__init__.py", "plugin.json", "HeadlessResliceBridge.py")
ZIP_TIMESTAMP = (2026, 11, 11, 0, 0, 0)


def build() -> bytes:
    template = TEMPLATE.read_text(encoding="utf-8")
    assignments = [
        node for node in ast.parse(template).body
        if isinstance(node, ast.Assign)
        and any(isinstance(target, ast.Name) and target.id == "BRIDGE_ZIP_B64"
                for target in node.targets)
    ]
    if len(assignments) != 1 or ast.literal_eval(assignments[0].value) != "":
        raise ValueError("The template must have exactly one empty BRIDGE_ZIP_B64 assignment.")

    archive = io.BytesIO()
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_STORED, compresslevel=6) as output:
        for name in MEMBERS:
            member = zipfile.ZipInfo(name, ZIP_TIMESTAMP)
            member.compress_type = zipfile.ZIP_STORED
            member.create_system = 3
            member.external_attr = 0o644 << 16
            output.writestr(member, (BRIDGE / name).read_bytes(),
                            compress_type=zipfile.ZIP_STORED)

    encoded = base64.b64encode(archive.getvalue()).decode("ascii")
    replacement = "BRIDGE_ZIP_B64 = (\n" + "".join(
        "    " + repr(encoded[offset:offset + 100]) + "\n"
        for offset in range(0, len(encoded), 100)
    ) + ")\n"
    assignment = assignments[0]
    lines = template.splitlines(keepends=True)
    source = "".join(lines[:assignment.lineno - 1]) + replacement + "".join(lines[assignment.end_lineno:])
    compile(source, str(OUTPUT), "exec")
    return source.encode("utf-8")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--check", action="store_true", help="Check the current standalone file without writing it.")
    args = parser.parse_args()
    try:
        expected = build()
        if args.check:
            if not OUTPUT.is_file() or OUTPUT.read_bytes() != expected:
                print("Standalone file is out of date. Run: python tools/build_standalone.py", file=sys.stderr)
                return 1
            print("OK: standalone launcher matches the source files.")
        else:
            OUTPUT.write_bytes(expected)
            print("Built: " + str(OUTPUT))
        return 0
    except (OSError, ValueError, SyntaxError) as exc:
        print("Build failed: " + str(exc), file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
