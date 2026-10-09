# image_to_dwg

画像（平面図・手書き図など）をClaude APIに送ってDXF図面を生成し、
必要であればODA File ConverterでDWGに変換するスクリプト。

## セットアップ

```bash
pip install -r requirements.txt
export ANTHROPIC_API_KEY=sk-ant-...
```

## 使い方

```bash
# DXFのみ生成（ODA File Converter不要）
python3 image_to_dwg.py drawing.png --skip-dwg

# DXF生成 + DWGに変換（ODA File Converterが必要）
python3 image_to_dwg.py drawing.png

# 出力先やモデル、ODA File Converterのパスを指定
python3 image_to_dwg.py drawing.png -o out.dxf --out-dwg out.dwg \
    --model claude-sonnet-5-5 --oda-path /path/to/ODAFileConverter
```

デフォルトでは `drawing.png` → `drawing.dxf` / `drawing.dwg` のように、
入力画像と同じ場所・同じ名前で出力する。

## DWG変換（ODA File Converter）

DXF→DWG変換には [ODA File Converter](https://www.opendesign.com/guestfiles/oda_file_converter)
（Windows/macOS/Linux対応、無料）が別途必要。インストール後、実行ファイルのパスを
`--oda-path` または環境変数 `ODA_CONVERTER_PATH` で指定する。未指定時はOSごとの
標準インストール先を推測する。

DWG変換が不要、またはODA File Converterを用意できない場合は `--skip-dwg` でDXF出力のみ行う。

## 制限事項

- Claudeが画像から読み取る寸法・形状はあくまで推定。生成されたDXF/DWGは必ず目視確認し、
  精密な製造・施工用途にそのまま使わないこと。
- 複雑な図面（多数の部品、注記、寸法線など）では、Claudeの出力が不完全なDXFになり
  構造検証（`ezdxf`）に失敗することがある。失敗時はエラーメッセージに生レスポンスが
  表示されるので、プロンプトの調整や画像の分割を検討する。
- `ezdxf` が未インストールの場合、DXFの構造検証はスキップされ警告が出る。
