Cura 5.12.0 ヘッドレス再スライス / v1.1.1
作成日: 2026-11-11

■ できること

Curaで保存したプロジェクト3MFを、Curaの画面を出さずに再スライスします。
G-codeは3MFと同じフォルダへ、拡張子だけを.gcodeに変えて保存します。

例:
  D:\3D\grip.3mf
    → D:\3D\grip.gcode
    → D:\3D\grip.reslice.log （処理ログ）

Cura本体のプロジェクト読込・設定評価・CuraEngine・GCodeWriterを使います。
PythonだけでCuraの設定を計算し直す方式ではありません。
補助プラグインはスクリプトに内蔵され、実行時に一時フォルダへ展開されます。
常用Curaへの手動プラグインインストールは不要です。

■ 必要なもの

・UltiMaker Cura 5.12.0 正式版（添付画像のバージョン）
・Python 3.10以上
・Curaの「ファイル → プロジェクトを保存」で保存した通常の3MF

Python用の追加パッケージは不要です。pip installも不要です。
Windows向けに実行ファイルを自動検出します。CuraEngine.exe単体は指定しません。
Curaを更新すると内部APIが変わる可能性があるため、この版は5.12.0に限定します。

■ 最初の実行

1. cura_reslice.pyを任意のフォルダへ保存します。
2. Curaで必要な設定と配置を調整し、「プロジェクトを保存」します。
3. PowerShellを開き、スクリプトを保存したフォルダで実行します。

  py -3 .\cura_reslice.py "D:\3D\grip.3mf"

Pythonの起動がpythonの場合:

  python .\cura_reslice.py "D:\3D\grip.3mf"

スクリプトは1ファイルだけでも実行できます。
ZIP内のHeadlessResliceBridgeフォルダは、内蔵プラグインの確認用ソースです。

■ Curaが自動検出されない場合

  py -3 .\cura_reslice.py "D:\3D\grip.3mf" --cura "C:\Program Files\UltiMaker Cura 5.12.0\UltiMaker-Cura.exe"

インストール先フォルダの指定もできます。

  py -3 .\cura_reslice.py "D:\3D\grip.3mf" --cura "C:\Program Files\UltiMaker Cura 5.12.0"

■ プリンタを変更する（v1.1.0追加）

最初に、Cura 5.12.0で使いたいプリンタを追加し、そのプリンタ用のノズル・材料・
印刷設定を保存してください。設定をディスクへ確実に保存するため、Curaを終了します。

保存済みプリンタの一覧:

  py -3 .\cura_reslice.py --list-printers

一覧取得の開始時に、Curaの起動・設定読み込みに時間がかかる旨を表示します。
そのまま一覧が表示されるまでお待ちください。

変更先の名前を指定して再スライス:

  py -3 .\cura_reslice.py "D:\3D\grip.3mf" --printer "Anycubic Kobra Go" --overwrite

上の名前は例です。実際には一覧に表示された名前をそのまま指定してください。
同名プリンタが複数ある場合は、一覧のIDを指定します。不明・曖昧な名前では停止します。

フォルダ全体を指定プリンタ向けに再生成:

  py -3 .\cura_reslice.py "D:\3D\models" --recursive --printer "Voxelab Aquila" --overwrite

プリンタを指定しない場合は、従来どおり3MF内のプリンタと設定を使います。

プリンタ指定時の設定の出どころ:
  ・プリンタ寸法、原点、開始・終了G-code: 変更先
  ・ノズル径、材料、品質、温度、速度、積層ピッチなど: 変更先の保存済み設定
  ・後処理スクリプト: 変更先の保存済み設定
  ・モデル形状、回転、スケール、Cura内の配置: 元3MF
  ・モデルごとの個別設定: 元3MF（全体設定より優先される場合があります）

「プリンタだけ指定して、元3MFの全体印刷設定をすべて残す」機能ではありません。
Curaでプロジェクトを開き、保存済みの別プリンタを選び直す動作に相当します。
積層ピッチや速度なども維持したい場合は、変更先プリンタ側に同じ値を保存してください。
設定が混ざらないよう、変更先の設定一式を使います。

配置はCuraシーン内の位置を保持します。ベッド寸法や原点の定義が違う場合、
出力G-codeのXY座標は変わります。自動配置・自動縮小は行いません。
変更先の造形範囲に収まらないモデルや押出機数が合わない組合せはエラーになります。

■ フォルダ一括処理

指定フォルダ直下の3MFをすべて処理:

  py -3 .\cura_reslice.py "D:\3D\models"

サブフォルダを含めて処理:

  py -3 .\cura_reslice.py "D:\3D\models" --recursive

複数ファイルを指定:

  py -3 .\cura_reslice.py "D:\3D\a.3mf" "D:\3D\b.3mf"

各3MFにつきCuraを1回起動して順番に処理します。
前の3MFの設定が次の3MFへ混ざらないよう、設定コピーも毎回作り直します。
1件の失敗後も残りを処理し、最後に成功・スキップ・失敗数を表示します。

■ 既存G-codeの扱い

標準では同名G-codeが存在すると、その3MFをスキップします。
再生成して置き換える場合:

  py -3 .\cura_reslice.py "D:\3D\models" --recursive --overwrite

再スライスと書き出しが成功した後に、完成したファイルを置き換えます。
読込失敗・スライス失敗・タイムアウト・中断の場合、元のG-codeを維持します。
元の3MFは変更しません。ログは実行のたびに更新します。

■ 引き継ぐ内容（--printer未指定時）

・3MFに保存されたプリンタ・ノズル・材料・印刷プロファイル
・温度、積層ピッチ、速度、インフィルなどの印刷設定
・開始G-codeと終了G-code
・モデルの配置・回転・スケール、モデルごとの設定
・復元可能なCura後処理スクリプト

--printer未指定の場合、実行時点でCura画面に表示中の未保存設定は使いません。
変更内容を反映するには、先に対象の3MFを保存してください。
不足する設定や後処理スクリプトが検出された場合、エラーで停止します。
出力の完全なバイト一致は保証しません。日時・ジョブ名などが変わり得ます。

■ 設定フォルダ

Windowsでは通常、次をコピー元として自動検出します。

  %APPDATA%\cura\5.12

常用設定は一時フォルダへコピーし、そのコピーでCuraを起動します。
ユーザープラグインと共有依存ファイルもコピーします。
通常使っているCuraの設定やプラグインを直接変更しません。
作業コピーでは、自動スライス・更新確認・使用状況送信・エンジンクラッシュ報告を
無効にします。追加プラグインを含むすべての通信を遮断する機能ではありません。
別の設定フォルダを使う場合:

  py -3 .\cura_reslice.py "D:\3D\grip.3mf" --config-dir "D:\CuraProfiles\5.12"

コピー元が自動検出されない場合は、Cura同梱の定義と3MF内の設定を使います。
独自プリンタや外部プラグインに依存するプロジェクトは、元の設定フォルダを指定してください。

■ 対象外・制限

・モデルだけを格納した3MF
・OrcaSlicer、Bambu Studio、PrusaSlicer等で保存したプロジェクト
・Universal Cura Project（UCP）
・複数のビルドプレートにモデルを置いたプロジェクト
・Cura 5.12.0以外の実行ファイル
・macOS（この版では設定分離が未対応）

他のCura 5.xで作成したプロジェクトは、まず5.12.0で正常に開けることを確認してください。
この版は通常のFDMプロジェクトを対象としています。レンダリングやQML画面が必要な
独自プラグインは、ヘッドレス動作に対応していない場合があります。
標準後処理以外の出力プラグイン固有の加工は、すべてを保証するものではありません。

■ エラー調査

対象3MFと同じフォルダに作られる「ファイル名.reslice.log」を確認してください。
ログ末尾のBRIDGE RESULTに、処理結果・設定値・モデル配置・後処理名が入ります。
起動自体が失敗した場合はBRIDGE RESULTがないことがあります。

1ファイルあたりの制限時間は900秒です。大きなモデルでは変更できます。

  py -3 .\cura_reslice.py "D:\3D\large.3mf" --timeout 3600

作業用の設定コピーを残して調査する場合:

  py -3 .\cura_reslice.py "D:\3D\grip.3mf" --keep-work

一時フォルダの場所がWORK行に表示されます。調査後はフォルダごと削除できます。

終了コード:
  0: 成功、または既存G-codeのためスキップのみ
  1: 1件以上の処理失敗
  2: 起動引数・入力・Cura検出などの事前エラー
  130: Ctrl+Cによる中断

■ Linuxでの検証用起動

LinuxのAppImageは、--headlessでもQt/OpenGL初期化を行います。
表示サーバーがない検証環境では、次の指定で実行しました。

  python3 cura_reslice.py model.3mf --cura /path/to/squashfs-root/AppRun --qt-platform offscreen --force-modern-opengl

--force-modern-openglは作業コピー内のCura設定だけを変更します。
OpenGLの描画機能を検証するオプションではありません。
Windowsでは通常、この2つの追加オプションは不要です。

■ 確認に使用した公式ソース

Cura 5.12.0: ヘッドレス起動と初期化
https://github.com/Ultimaker/Cura/blob/5.12.0/cura/CuraApplication.py

Cura 5.12.0: プロジェクト読込
https://github.com/Ultimaker/Cura/blob/5.12.0/plugins/3MFReader/ThreeMFWorkspaceReader.py

Cura 5.12.0: スライス処理
https://github.com/Ultimaker/Cura/blob/5.12.0/plugins/CuraEngineBackend/CuraEngineBackend.py

Cura 5.12.0: G-code書き出しと後処理
https://github.com/Ultimaker/Cura/blob/5.12.0/plugins/GCodeWriter/GCodeWriter.py
https://github.com/Ultimaker/Cura/blob/5.12.0/plugins/PostProcessingPlugin/PostProcessingPlugin.py

Cura 5.12.0: 保存済みプリンタの切替
https://github.com/Ultimaker/Cura/blob/5.12.0/cura/Settings/MachineManager.py

Uranium 5.12.0: 設定ディレクトリ・CLI・Qt起動
https://github.com/Ultimaker/Uranium/blob/5.12.0/UM/Resources.py
https://github.com/Ultimaker/Uranium/blob/5.12.0/UM/Application.py
https://github.com/Ultimaker/Uranium/blob/5.12.0/UM/Qt/QtApplication.py

これは上記APIを利用する自作ツールです。UltiMaker公式CLI製品ではありません。
公式Cura/Engine本体は配布物に含めず、利用者がインストールしたものを使用します。

■ v1.0.0の検証結果（2026-11-11）

公式Linux版Cura 5.12.0 AppImageを実行して確認しました。
Windows版そのものは未実行です。

[合格] 単独のcura_reslice.pyだけでヘッドレス再スライス→同一フォルダ保存
[合格] 日本語と空白を含む入力パス
[合格] 2モデルの配置、モデルごとのインフィル設定
[合格] ノズル213℃・ベッド57℃の設定と加熱待ち
[合格] 初層0.20mm・以降0.24mm、25層
[合格] 開始・終了G-codeの保存内容
[合格] SearchAndReplace後処理の実行
[合格] 不足する後処理スクリプトのエラー停止
[合格] 造形範囲外モデルのエラー停止
[合格] 既存G-codeの標準スキップ
[合格] --overwrite指定中でも、処理失敗時は既存G-codeが不変
[合格] UCP・モデルのみの3MFを拒否
[合格] コピー元の設定・データファイルの内容が処理前後で不変

最終の単独スクリプト試験: 2個の6mm立方体、後処理・個別設定あり。
出力48,619 bytes、処理12.4秒。この秒数は検証環境での参考値です。
多素材・特殊出力プラグイン・すべてのプリンタ定義を網羅した検証ではありません。

■ v1.1.0の検証結果（2026-11-11）

公式Linux版Cura 5.12.0で、保存済みプリンタAからBへの変更を実行しました。
保存済みプリンタ一覧の取得、対象選択、モデル再割当て、再スライスに成功しています。

検証用プリンタの設定:
  A: ベッド200×200mm、ノズル0.4mm、積層0.24mm、ノズル213℃、ベッド57℃
  B: ベッド220×220mm、ノズル0.6mm、積層0.16mm、ノズル225℃、ベッド64℃

実際の出力・設定照合:
  ・Bのノズル0.6mm、速度35mm/s、全体インフィル11%を使用
  ・Bの開始・終了G-code、加熱設定と加熱待ちを使用
  ・2モデルの配置を保持し、Bの押出機へ再割当て
  ・元3MFのモデル個別インフィル23%を保持
  ・Bで直接生成した基準G-codeと、コメントを除いた全2,265命令が一致
  ・保存済み設定の照合時、Curaが自動生成する有効押出機数は計算値で別途検証

出力62,411 bytes、処理27.7秒。この秒数は検証環境での参考値です。
ソース版と単独配布版に内蔵されたブリッジのバイト一致、Python構文、ZIP整合性も確認しました。
Windows版そのもの、複数押出機間の切替、すべての機種定義は未検証です。

追加の異常系確認（単独配布スクリプトを使用）:
  ・不明なプリンタ名をエラーで拒否
  ・幅210mmのモデルを200mmベッドへ変更すると造形範囲外で停止
  ・上記どちらも--overwrite指定下で既存G-codeのSHA256が不変
  ・元の設定・データ全27ファイルのSHA256が処理前後で不変
