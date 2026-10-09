#!/usr/bin/env python3
"""Convert a 2D drawing image into a DXF file via the Claude API, and optionally
convert that DXF into a DWG file using the ODA File Converter.

Usage:
    python3 image_to_dwg.py drawing.png
    python3 image_to_dwg.py drawing.png --skip-dwg
    python3 image_to_dwg.py drawing.png --oda-path /path/to/ODAFileConverter
"""

import argparse
import base64
import io
import mimetypes
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

import requests

try:
    import ezdxf
    from ezdxf.lldxf.const import DXFStructureError

    _HAS_EZDXF = True
except ImportError:
    _HAS_EZDXF = False

ANTHROPIC_API_URL = "https://api.anthropic.com/v1/messages"
ANTHROPIC_VERSION = "2023-06-01"
DEFAULT_MODEL = "claude-sonnet-5-5"
SUPPORTED_IMAGE_TYPES = {"image/png", "image/jpeg", "image/webp", "image/gif"}

PROMPT = """あなたはCAD設計支援AIです。
添付画像を解析し、AutoCADのDXF形式で図面を出力してください。

要件:
- 単位はmm
- 2D図形のみ
- 座標系は左下原点
- DXFはASCII形式（R12〜2018互換のテキスト形式）
- 出力は ```dxf ... ``` のコードブロック1つのみとし、説明文は含めない

注意: 画像から読み取れる寸法はあくまで推定です。正確な寸法が必要な場合は、
生成されたDXFを必ず目視確認してください。"""


def get_api_key() -> str:
    key = os.getenv("ANTHROPIC_API_KEY") or os.getenv("CLAUDE_API_KEY")
    if not key:
        raise SystemExit(
            "ANTHROPIC_API_KEY (or CLAUDE_API_KEY) environment variable is not set."
        )
    return key


def guess_media_type(image_path: Path) -> str:
    mime, _ = mimetypes.guess_type(image_path.name)
    if mime not in SUPPORTED_IMAGE_TYPES:
        raise ValueError(
            f"Unsupported image type for {image_path.name!r}: {mime!r}. "
            f"Use one of {sorted(SUPPORTED_IMAGE_TYPES)}."
        )
    return mime


def extract_dxf(text: str) -> str:
    """Pull the DXF text out of Claude's reply, tolerating a missing ```dxf tag."""
    fenced_blocks = re.findall(r"```([a-zA-Z0-9_-]*)\s*\n(.*?)```", text, re.DOTALL)

    for lang, block in fenced_blocks:
        if lang.lower() == "dxf":
            return block.strip()

    for _, block in fenced_blocks:
        if re.search(r"^\s*0\s*\n\s*SECTION", block, re.MULTILINE):
            return block.strip()

    raise ValueError(
        "No DXF content found in Claude's response. Full response:\n" + text
    )


def validate_dxf(dxf_content: str) -> None:
    if not _HAS_EZDXF:
        print("[WARN] ezdxf is not installed; skipping DXF validation (pip install ezdxf to enable).")
        return
    try:
        ezdxf.read(io.StringIO(dxf_content))
    except DXFStructureError as exc:
        raise ValueError(f"Claude returned DXF that failed structural validation: {exc}") from exc


def image_to_dxf(image_path: Path, dxf_path: Path, model: str = DEFAULT_MODEL) -> None:
    media_type = guess_media_type(image_path)
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")

    headers = {
        "x-api-key": get_api_key(),
        "anthropic-version": ANTHROPIC_VERSION,
        "content-type": "application/json",
    }
    payload = {
        "model": model,
        "max_tokens": 4096,
        "messages": [
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {
                        "type": "image",
                        "source": {"type": "base64", "media_type": media_type, "data": image_b64},
                    },
                ],
            }
        ],
    }

    response = requests.post(ANTHROPIC_API_URL, headers=headers, json=payload, timeout=120)
    try:
        response.raise_for_status()
    except requests.HTTPError as exc:
        raise RuntimeError(f"Claude API request failed: {exc}\n{response.text}") from exc

    data = response.json()
    text = "".join(
        block.get("text", "") for block in data.get("content", []) if block.get("type") == "text"
    )

    dxf_content = extract_dxf(text)
    validate_dxf(dxf_content)

    dxf_path.parent.mkdir(parents=True, exist_ok=True)
    dxf_path.write_text(dxf_content, encoding="utf-8")
    print(f"[OK] DXF written: {dxf_path}")


def default_oda_path() -> str:
    env = os.getenv("ODA_CONVERTER_PATH")
    if env:
        return env
    if sys.platform.startswith("win"):
        return r"C:\Program Files\ODA\ODAFileConverter\ODAFileConverter.exe"
    if sys.platform == "darwin":
        return "/Applications/ODAFileConverter.app/Contents/MacOS/ODAFileConverter"
    return "/usr/bin/ODAFileConverter"


def convert_dxf_to_dwg(
    dxf_path: Path, dwg_path: Path, oda_path: str, out_version: str = "ACAD2018"
) -> None:
    if not os.path.isfile(oda_path):
        raise FileNotFoundError(
            f"ODA File Converter not found at {oda_path!r}. Install it from "
            "https://www.opendesign.com/guestfiles/oda_file_converter, then pass "
            "--oda-path or set the ODA_CONVERTER_PATH environment variable."
        )

    with tempfile.TemporaryDirectory() as in_dir, tempfile.TemporaryDirectory() as out_dir:
        staged_dxf = Path(in_dir) / dxf_path.name
        shutil.copy(dxf_path, staged_dxf)

        # ODA File Converter CLI signature:
        #   <in_folder> <out_folder> <out_version> <out_type> <recurse> <audit> [filter]
        cmd = [
            oda_path,
            in_dir,
            out_dir,
            out_version,
            "DWG",
            "0",  # do not recurse
            "1",  # audit each file
            "*.dxf",
        ]
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
        if result.returncode != 0:
            raise RuntimeError(
                f"ODA File Converter exited with code {result.returncode}\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )

        produced = Path(out_dir) / (staged_dxf.stem + ".dwg")
        if not produced.exists():
            raise RuntimeError(
                f"ODA File Converter did not produce {produced.name} in {out_dir}\n"
                f"stdout: {result.stdout}\nstderr: {result.stderr}"
            )

        dwg_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.move(str(produced), dwg_path)

    print(f"[OK] DWG written: {dwg_path}")


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Convert a drawing image to DXF (and optionally DWG) via the Claude API."
    )
    parser.add_argument("image", type=Path, help="Input image file (PNG/JPEG/WEBP/GIF)")
    parser.add_argument(
        "-o", "--out-dxf", type=Path, default=None,
        help="Output DXF path (default: <image_stem>.dxf)",
    )
    parser.add_argument(
        "--out-dwg", type=Path, default=None,
        help="Output DWG path (default: <image_stem>.dwg)",
    )
    parser.add_argument("--model", default=DEFAULT_MODEL, help=f"Claude model id (default: {DEFAULT_MODEL})")
    parser.add_argument(
        "--skip-dwg", action="store_true",
        help="Only generate the DXF; skip the DWG conversion step",
    )
    parser.add_argument("--oda-path", default=None, help="Path to the ODA File Converter executable")
    parser.add_argument(
        "--dwg-version", default="ACAD2018",
        help="Output DWG version understood by ODA File Converter (default: ACAD2018)",
    )
    args = parser.parse_args()

    if not args.image.is_file():
        raise SystemExit(f"Input image not found: {args.image}")

    dxf_path = args.out_dxf or args.image.with_suffix(".dxf")
    dwg_path = args.out_dwg or args.image.with_suffix(".dwg")

    image_to_dxf(args.image, dxf_path, model=args.model)

    if args.skip_dwg:
        print("[INFO] --skip-dwg set; leaving DXF-only output.")
        return

    oda_path = args.oda_path or default_oda_path()
    convert_dxf_to_dwg(dxf_path, dwg_path, oda_path, out_version=args.dwg_version)


if __name__ == "__main__":
    main()
