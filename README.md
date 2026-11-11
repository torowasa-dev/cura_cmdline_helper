# cura_cmdline_helper

UltiMaker Cura **5.12.0**で保存したプロジェクト3MFを、画面を開かずに再スライスするPythonツールです。生成したG-codeを、元3MFと同じフォルダへ保存します。

**現在のバージョン: 1.1.0**

## 必要なもの

- UltiMaker Cura 5.12.0正式版
- Python 3.10以上（追加パッケージ不要）
- Curaの「ファイル → プロジェクトを保存」で作成した通常の3MF

実行にはルートの[`cura_reslice.py`](cura_reslice.py)だけを使えます。補助プラグインを内蔵しており、Curaへの手動インストールは不要です。Cura・CuraEngine本体は含みません。

## 使い方

WindowsのPowerShellで実行します。`py`がない環境では`python`に置き換えてください。

```powershell
# 1ファイルを再スライス
py -3 .\cura_reslice.py "D:\3D\grip.3mf"

# サブフォルダを含めて再生成し、既存G-codeを置換
py -3 .\cura_reslice.py "D:\3D\models" --recursive --overwrite

# 保存済みプリンタ名・IDを確認
py -3 .\cura_reslice.py --list-printers

# 保存済みの別プリンタ向けに再スライス
py -3 .\cura_reslice.py "D:\3D\grip.3mf" --printer "Anycubic Kobra Go" --overwrite
```

プリンタ名は例です。実際には一覧に表示された名前またはIDを指定します。変更先のプリンタ・材料・印刷設定は、事前にCuraで保存してCuraを終了してください。

Curaが自動検出されない場合は、実行ファイルを指定します。

```powershell
py -3 .\cura_reslice.py "D:\3D\grip.3mf" --cura "C:\Program Files\UltiMaker Cura 5.12.0\UltiMaker-Cura.exe"
```

## コマンド・オプション一覧

```powershell
py -3 .\cura_reslice.py --help
```

| オプション | 内容 |
|---|---|
| `--help` / `-h` | 全オプションを表示 |
| `--version` | スクリプトと対象Curaのバージョンを表示 |
| `--list-printers` | 保存済みプリンタの一覧。入力ファイル・`--printer`とは併用不可 |
| `--printer "名前またはID"` | 変更先プリンタを指定 |
| `--recursive` / `-r` | サブフォルダも検索 |
| `--overwrite` | 成功後に既存G-codeを置換 |
| `--cura "パス"` | Cura実行ファイルまたはインストール先を指定 |
| `--config-dir "パス"` | コピー元のCura設定フォルダを指定 |
| `--data-dir "パス"` | Linux用のコピー元データフォルダを指定 |
| `--timeout 秒数` | 1件の制限時間。既定900秒 |
| `--keep-work` | 調査用の一時フォルダを残す |
| `--qt-platform offscreen` | Linuxの表示サーバーなし環境で使用 |
| `--force-modern-opengl` | 作業用設定でOpenGLの自動検出を省略。通常は不要 |

## 出力と設定の扱い

| 項目 | 動作 |
|---|---|
| 出力 | `grip.3mf`と同じフォルダへ`grip.gcode`と`grip.reslice.log`を保存 |
| 既存G-code | 標準ではスキップ。`--overwrite`で成功時だけ置換 |
| 元3MF・常用設定 | 元3MFは変更せず、Cura設定の一時コピーで処理 |
| `--printer`未指定 | 元3MFのプリンタ・印刷設定を使用 |
| `--printer`指定時 | 変更先の保存済みプリンタ設定・材料・品質・温度・速度・開始終了G-code・後処理を使用 |
| モデル | 元3MFの形状・回転・スケール・Cura内の配置・モデル別設定を保持 |

モデル別設定は変更先の全体設定より優先される場合があります。プリンタを変更すると、積層ピッチや速度なども変更先の保存値になります。元の全体印刷設定を残して機種だけ差し替える機能ではありません。

自動配置・自動縮小は行いません。造形範囲外や押出機数の不一致はエラーにします。ベッド寸法・原点の違いによって、Cura内の配置が同じでも出力G-codeのXY座標は変わります。

## 対応範囲と検証

公式Linux版Cura 5.12.0 AppImageで、再スライス、モデル別設定、後処理、保存済みプリンタの変更、失敗時の既存ファイル保護を確認しています。**Windows版そのものは未実行です。** 複数押出機間の変更や、すべてのプリンタ定義・独自プラグインを検証したものではありません。

モデルだけの3MF、他スライサーのプロジェクト、UCP、複数のビルドプレート、Cura 5.12.0以外、macOSは対象外です。詳細なオプション・制限・検証結果は[`README_JA.txt`](README_JA.txt)を参照してください。

## ソースと再生成

| ファイル | 用途 |
|---|---|
| [`cura_reslice.py`](cura_reslice.py) | 単独実行できる配布版 |
| [`src/cura_reslice.py`](src/cura_reslice.py) | ランチャーの編集元 |
| [`src/HeadlessResliceBridge/`](src/HeadlessResliceBridge/) | Cura内部で動く補助プラグインの編集元 |
| [`tools/build_standalone.py`](tools/build_standalone.py) | 補助プラグインを埋め込み、配布版を再生成 |
| [`reslice_drag_drop.bat`](reslice_drag_drop.bat) | Windows用ドラッグ＆ドロップ実行補助 |

編集元を変更したら、配布版を再生成します。ビルド処理はCuraを起動しません。

```powershell
python tools/build_standalone.py
python tools/build_standalone.py --check
python cura_reslice.py --help
```

`--check`はソースと配布版の一致を確認し、不一致なら終了コード1を返します。

このツールはCuraの内部APIを利用する自作ツールであり、UltiMaker公式のCLI製品ではありません。
