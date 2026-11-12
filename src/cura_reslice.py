#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""Re-slice full Cura 5.12.0 projects without opening a Cura window.

Python 3.10+; standard library only. The embedded bridge runs inside the
installed Cura, using its project reader, settings evaluation and CuraEngine.
See README_JA.txt for scope, examples and the integration-test limitations.
"""
from __future__ import annotations

import argparse
import base64
import configparser
import io
import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import uuid
import zipfile

SCRIPT_VERSION = "1.2.0"
CURA_VERSION = "5.12.0"
PROFILE_VERSION = "5.12"
BRIDGE_NAME = "HeadlessResliceBridge"
# The release builder fills this with the matching, reviewable bridge source.
BRIDGE_ZIP_B64 = ""


class ResliceError(Exception):
    pass


def emit(message: str) -> None:
    print(message, flush=True)


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="Cura 5.12.0のプロジェクト3MFを画面なしで再スライスします。",
        epilog="入力3MFは変更しません。G-codeは入力と同じフォルダ・同じ名前で保存します。",
    )
    p.add_argument("paths", nargs="*", type=Path, help="3MFファイル、または3MFを含むフォルダ（複数指定可）")
    p.add_argument("--cura", type=Path, help="Cura実行ファイルまたはインストール先。Windowsでは自動検出")
    p.add_argument("--config-dir", type=Path, help="コピー元のCura 5.12設定フォルダ。通常は自動検出")
    p.add_argument("--data-dir", type=Path, help="Linux用のコピー元データフォルダ")
    p.add_argument("--printer", help="Curaに保存済みの変更先プリンタ名またはID。変更先の設定一式を使用")
    p.add_argument("--insert-before-end", type=Path, metavar="GCODE", help="指定G-codeファイルをCuraの終了G-code直前へ挿入")
    p.add_argument("--list-printers", action="store_true", help="Curaに保存済みのプリンタ名・IDを表示して終了")
    p.add_argument("--recursive", "-r", action="store_true", help="指定フォルダのサブフォルダも処理")
    p.add_argument("--overwrite", action="store_true", help="成功時に既存の同名G-codeを置換する")
    p.add_argument("--timeout", type=float, default=900.0, metavar="SECONDS", help="1ファイルの制限時間。既定900秒")
    p.add_argument("--keep-work", action="store_true", help="調査用に一時フォルダを残す（設定のコピーを含む）")
    p.add_argument("--qt-platform", help="Qtバックエンドの明示指定。通常不要。Linux検証例: offscreen")
    p.add_argument("--force-modern-opengl", action="store_true", help="作業コピー内でCuraのOpenGL検出を省略。通常不要")
    p.add_argument("--version", action="version", version=f"%(prog)s {SCRIPT_VERSION} / Cura {CURA_VERSION}")
    return p


def collect_inputs(paths: list[Path], recursive: bool) -> list[Path]:
    found: dict[str, Path] = {}
    for value in paths:
        path = value.expanduser().resolve()
        if path.is_dir():
            candidates = path.rglob("*") if recursive else path.iterdir()
            files = [x for x in candidates if x.is_file() and x.suffix.lower() == ".3mf"]
        elif path.is_file() and path.suffix.lower() == ".3mf":
            files = [path]
        else:
            raise ResliceError(f"3MFファイル／フォルダが見つかりません: {path}")
        for file in files:
            resolved = file.resolve()
            found[os.path.normcase(str(resolved))] = resolved
    if not found:
        raise ResliceError("対象の3MFファイルがありません。")
    return sorted(found.values(), key=lambda x: str(x).casefold())



def validate_insert_gcode(path: Path | None) -> Path | None:
    """Validate an optional UTF-8/ASCII G-code snippet before Cura starts."""
    if path is None:
        return None
    resolved = path.expanduser().resolve()
    if not resolved.is_file():
        raise ResliceError(f"挿入G-codeファイルが見つかりません: {resolved}")
    try:
        raw = resolved.read_bytes()
    except OSError as exc:
        raise ResliceError(f"挿入G-codeファイルを読めません: {resolved}: {exc}") from exc
    if len(raw) > 1024 * 1024:
        raise ResliceError("挿入G-codeファイルは1MiB以下にしてください。")
    if not raw.strip():
        raise ResliceError("挿入G-codeファイルが空です。")
    if b"\x00" in raw:
        raise ResliceError("挿入G-codeファイルにNUL文字が含まれています。")
    try:
        raw.decode("utf-8-sig")
    except UnicodeDecodeError as exc:
        raise ResliceError("挿入G-codeファイルはUTF-8またはASCIIで保存してください。") from exc
    return resolved


def validate_project(path: Path) -> None:
    """Require a full Cura workspace, not a mesh-only or other-slicer 3MF."""
    try:
        with zipfile.ZipFile(path) as z:
            names = z.namelist()
            if len(names) != len(set(names)):
                raise ResliceError("重複したZIPメンバーを持つ3MFは処理できません。")
            if not any(n.lower().endswith(".model") for n in names):
                raise ResliceError("3MF内にモデルがありません。")
            if "Cura/user-settings.json" in names:
                raise ResliceError("UCP（Universal Cura Project）は対象外です。通常のCuraプロジェクトとして保存してください。")
            if "Cura/preferences.cfg" not in names:
                raise ResliceError("Curaプロジェクトのpreferences.cfgがありません。Curaでプロジェクトを保存し直してください。")
            global_cfg = [n for n in names if n.startswith("Cura/") and n.endswith(".global.cfg")]
            if not global_cfg:
                raise ResliceError(
                    "プリンタ・印刷設定を含むCuraプロジェクトではありません。"
                    "Cura 5.12.0で［ファイル→プロジェクトを保存］した3MFを指定してください。"
                    "モデルだけの3MF、他スライサーの3MF、UCPは対象外です。"
                )
            if len(global_cfg) != 1:
                raise ResliceError("複数のプリンタ設定を含む3MFはこの版の対象外です。")
            info = z.getinfo(global_cfg[0])
            if info.file_size > 4 * 1024 * 1024:
                raise ResliceError("プリンタ設定ファイルが想定サイズを超えています。")
            cfg = configparser.ConfigParser(interpolation=None, strict=False)
            cfg.read_string(z.read(global_cfg[0]).decode("utf-8-sig"))
            if not cfg.has_section("containers"):
                raise ResliceError("Curaプロジェクトの設定スタックが欠落しています。")
    except (zipfile.BadZipFile, UnicodeError, configparser.Error) as exc:
        raise ResliceError(f"3MFを読めません: {exc}") from exc


def find_cura(explicit: Path | None) -> Path:
    candidates: list[Path] = []
    if explicit:
        candidates.append(explicit.expanduser())
    elif os.environ.get("CURA_EXE"):
        candidates.append(Path(os.environ["CURA_EXE"]))
    elif os.name == "nt":
        roots = [os.environ.get("ProgramFiles"), os.environ.get("ProgramFiles(x86)"),
                 str(Path(os.environ.get("LOCALAPPDATA", "")) / "Programs")]
        for root in roots:
            if root and Path(root).is_dir():
                candidates.extend(sorted(Path(root).glob("*Cura*5.12*"), reverse=True))
    else:
        found = shutil.which("cura") or shutil.which("UltiMaker-Cura")
        if found:
            candidates.append(Path(found))
    for candidate in candidates:
        if candidate.is_dir():
            for name in ("UltiMaker-Cura.exe", "Ultimaker-Cura.exe", "cura.exe", "AppRun", "UltiMaker-Cura"):
                executable = candidate / name
                if executable.is_file():
                    return executable.absolute()
        elif candidate.is_file():
            # Do not resolve AppRun symlinks: AppImage launchers can depend on argv[0].
            return candidate.absolute()
    raise ResliceError(
        "Cura 5.12.0が見つかりません。--cura で実行ファイルを指定してください。\n"
        '例: --cura "C:\\Program Files\\UltiMaker Cura 5.12.0\\UltiMaker-Cura.exe"'
    )


def profile_sources(args: argparse.Namespace) -> tuple[Path | None, Path | None]:
    if sys.platform == "darwin":
        raise ResliceError("この版の設定分離はWindowsとLinux用です。macOSには対応していません。")
    if os.name == "nt":
        if args.data_dir:
            raise ResliceError("Windowsでは --data-dir は使いません。--config-dir を指定してください。")
        default_cfg = Path(os.environ.get("APPDATA", "")) / "cura" / PROFILE_VERSION
        default_data = default_cfg
    else:
        default_cfg = Path(os.environ.get("XDG_CONFIG_HOME", str(Path.home() / ".config"))) / "cura" / PROFILE_VERSION
        default_data = Path(os.environ.get("XDG_DATA_HOME", str(Path.home() / ".local/share"))) / "cura" / PROFILE_VERSION
    config = args.config_dir.expanduser().resolve() if args.config_dir else default_cfg
    data = args.data_dir.expanduser().resolve() if args.data_dir else (config if os.name == "nt" else default_data)
    for label, path, explicit in (("設定", config, args.config_dir), ("データ", data, args.data_dir)):
        if explicit and not path.is_dir():
            raise ResliceError(f"{label}フォルダがありません: {path}")
    return (config if config.is_dir() else None, data if data.is_dir() else None)


def copy_profile(source: Path | None, destination: Path) -> None:
    destination.mkdir(parents=True, exist_ok=True)
    if source is not None:
        def ignore(directory: str, names: list[str]) -> set[str]:
            ignored = {name for name in names if name in {"__pycache__", ".git"}}
            if Path(directory) == source:
                ignored.update(name for name in names if name in {"cache", "crash", "backups"}
                               or name.endswith(".lock") or name.startswith("cura.log"))
            return ignored
        shutil.copytree(source, destination, dirs_exist_ok=True, ignore=ignore)


def configure_profile(path: Path, args: argparse.Namespace) -> None:
    cfg_path = path / "cura.cfg"
    cfg = configparser.ConfigParser(interpolation=None, strict=False)
    if cfg_path.exists():
        cfg.read(cfg_path, encoding="utf-8")
    changes = {
        "general": {"version": "7", "auto_slice": "False", "plugins_to_remove": ""},
        "cura": {"single_instance": "False", "active_machine": ""},
        "metadata": {"setting_version": "26"},
        "info": {"send_engine_crash": "False", "send_slice_info": "False",
                 "automatic_plugin_update_check": "False", "automatic_update_check": "False"},
    }
    if args.force_modern_opengl:
        changes["view"] = {"opengl_version_detect": "force_modern"}
    for section, values in changes.items():
        if not cfg.has_section(section):
            cfg.add_section(section)
        for key, value in values.items():
            cfg.set(section, key, value)
    disabled = [x.strip() for x in cfg.get("general", "disabled_plugins", fallback="").split(",") if x.strip()]
    if BRIDGE_NAME in disabled:
        disabled.remove(BRIDGE_NAME)
    cfg.set("general", "disabled_plugins", ",".join(disabled))
    with cfg_path.open("w", encoding="utf-8", newline="\n") as stream:
        cfg.write(stream)
    plugin_cfg = path / "plugins.json"
    if plugin_cfg.exists():
        data = json.loads(plugin_cfg.read_text(encoding="utf-8"))
        data["disabled"] = [name for name in data.get("disabled", []) if name != BRIDGE_NAME]
        # A batch run must not carry out pending package installation/removal.
        data["to_install"] = {}
        data["to_remove"] = []
        plugin_cfg.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    sanitize_package_actions(path)


def sanitize_package_actions(path: Path) -> None:
    package_cfg = path / "packages.json"
    if package_cfg.exists():
        data = json.loads(package_cfg.read_text(encoding="utf-8"))
        for key in ("to_install", "to_remove"):
            if key in data:
                data[key] = {} if isinstance(data[key], dict) else []
        package_cfg.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")


def install_bridge(data_path: Path) -> None:
    destination = data_path / "plugins" / BRIDGE_NAME
    if destination.exists():
        shutil.rmtree(destination)  # Only inside this run's temporary copy.
    destination.mkdir(parents=True)
    if BRIDGE_ZIP_B64:
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(BRIDGE_ZIP_B64))) as z:
            for member in z.infolist():
                if member.filename not in {"__init__.py", "plugin.json", "HeadlessResliceBridge.py"}:
                    raise ResliceError("内蔵ブリッジのファイル構成が不正です。")
                (destination / member.filename).write_bytes(z.read(member))
    else:
        source = Path(__file__).resolve().parent / BRIDGE_NAME
        if not source.is_dir():
            raise ResliceError("ブリッジがありません。配布ZIPを展開し直してください。")
        for name in ("__init__.py", "plugin.json", "HeadlessResliceBridge.py"):
            shutil.copy2(source / name, destination / name)


def build_environment(work: Path, sources: tuple[Path | None, Path | None], args: argparse.Namespace) -> dict[str, str]:
    env = os.environ.copy()
    if os.name == "nt":
        env["APPDATA"] = str(work / "roaming")
        env["LOCALAPPDATA"] = str(work / "local")
        config = Path(env["APPDATA"]) / "cura" / PROFILE_VERSION
        data = config
    else:
        env["XDG_CONFIG_HOME"] = str(work / "config")
        env["XDG_DATA_HOME"] = str(work / "data")
        env["XDG_CACHE_HOME"] = str(work / "cache")
        config = Path(env["XDG_CONFIG_HOME"]) / "cura" / PROFILE_VERSION
        data = Path(env["XDG_DATA_HOME"]) / "cura" / PROFILE_VERSION
    copy_profile(sources[0], config)
    if data != config:
        copy_profile(sources[1], data)
    configure_profile(config, args)
    if data != config:
        sanitize_package_actions(data)
    # Installed plug-ins may keep larger dependencies next to the version dir.
    source_data = sources[1] if data != config else sources[0]
    if source_data is not None:
        shared_storage = source_data.parent / "storage"
        if shared_storage.is_dir():
            shutil.copytree(shared_storage, data.parent / "storage", dirs_exist_ok=True)
    install_bridge(data)
    env["CURA_HEADLESS_RESLICE_JOB"] = str(work / "job.json")
    env["PYTHONIOENCODING"] = "utf-8"
    # Cura's official preference also sets this before starting CuraEngine.
    # Setting it here disables engine crash reporting during early startup too.
    env["USE_SENTRY"] = "0"
    # Do not inherit a Python/Qt toolchain from the invoking shell into Cura.
    for key in ("PYTHONHOME", "PYTHONPATH"):
        env.pop(key, None)
    return env


def stop_child(process: subprocess.Popen) -> None:
    if os.name == "nt":
        if process.poll() is None:
            subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, check=False)
    else:
        try:
            os.killpg(process.pid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    try:
        process.wait(timeout=5)
    except subprocess.TimeoutExpired:
        if os.name != "nt":
            try:
                os.killpg(process.pid, signal.SIGKILL)
            except ProcessLookupError:
                pass
        else:
            process.kill()
        process.wait(timeout=5)


def publish_gcode(temporary: Path, target: Path, overwrite: bool) -> None:
    """Commit only a completed file, without a no-overwrite check/rename race."""
    if overwrite:
        os.replace(temporary, target)
        return
    if os.name == "nt":
        # On Windows os.rename fails if destination already exists.
        os.rename(temporary, target)
    else:
        # Atomic creation, fails with EEXIST; both paths are on the same volume.
        os.link(temporary, target)
        temporary.unlink()


def run_cura_job(job: dict, work: Path, log_path: Path, cura: Path,
                 env: dict[str, str], args: argparse.Namespace) -> dict:
    (work / "job.json").write_text(json.dumps(job, ensure_ascii=False, indent=2), encoding="utf-8")
    command = [str(cura), "--headless", "--debug"]
    if args.qt_platform:
        command.extend(["-platform", args.qt_platform])
    kwargs: dict = {"env": env, "cwd": str(cura.parent), "stderr": subprocess.STDOUT}
    if os.name == "nt":
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    else:
        kwargs["start_new_session"] = True
    with log_path.open("w", encoding="utf-8", newline="\n") as log:
        log.write(f"cura_reslice {SCRIPT_VERSION}\nAction: {job['action']}\n"
                  f"Input: {job.get('input_path', '')}\nPrinter: {job.get('printer') or '(project)'}\n"
                  f"Cura: {cura}\nWork: {work}\n")
        log.flush()
        with subprocess.Popen(command, stdout=log, **kwargs) as process:
            try:
                process.wait(timeout=args.timeout)
            except subprocess.TimeoutExpired:
                stop_child(process)
                raise ResliceError(f"{args.timeout:g}秒でタイムアウトしました。ログ: {log_path}")
            except BaseException:
                stop_child(process)
                raise
            return_code = process.returncode
    result_path = work / "result.json"
    if not result_path.is_file():
        raise ResliceError(f"Curaが処理結果を返さず終了しました（終了コード {return_code}）。ログ: {log_path}")
    result = json.loads(result_path.read_text(encoding="utf-8"))
    with log_path.open("a", encoding="utf-8") as log:
        log.write("\nBRIDGE RESULT\n" + json.dumps(result, ensure_ascii=False, indent=2) + "\n")
    if result.get("job_id") != job["job_id"]:
        raise ResliceError(f"処理結果のジョブIDが一致しません。ログ: {log_path}")
    if not result.get("success"):
        raise ResliceError(f"{result.get('error', 'Curaでの処理に失敗しました。')}\nログ: {log_path}")
    if return_code != 0:
        raise ResliceError(f"Curaが異常終了しました（終了コード {return_code}）。ログ: {log_path}")
    return result


def list_printers(cura: Path, sources: tuple[Path | None, Path | None], args: argparse.Namespace) -> int:
    emit("INFO: Curaを起動して設定を読み込むため、プリンタ一覧の表示まで時間がかかります。しばらくお待ちください。")
    work = Path(tempfile.mkdtemp(prefix="cura-printers-"))
    completed = False
    try:
        env = build_environment(work, sources, args)
        job = {"job_id": uuid.uuid4().hex, "action": "list_printers",
               "result_path": str(work / "result.json"),
               "timeout_seconds": args.timeout, "expected_version": CURA_VERSION}
        result = run_cura_job(job, work, work / "printers.log", cura, env, args)
        printers = result.get("printers", [])
        emit(f"Cura {CURA_VERSION} / 登録済みプリンタ {len(printers)} 件")
        for printer in printers:
            emit(f"{printer['name']}\n  ID: {printer['id']}\n"
                 f"  定義: {printer.get('definition', '')} / 押出機数: {printer.get('extruder_count', '?')}")
        if not printers:
            emit("Cura 5.12.0で使用するプリンタを追加・設定し、Curaを終了してから再実行してください。")
        completed = True
        return 0
    finally:
        if args.keep_work or not completed:
            emit(f"WORK: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


def reslice_one(source: Path, cura: Path, sources: tuple[Path | None, Path | None], args: argparse.Namespace) -> str:
    target = source.with_suffix(".gcode")
    if target.exists() and not args.overwrite:
        emit(f"SKIP: {target.name}（既存。置換する場合は --overwrite）")
        return "skipped"
    validate_project(source)
    work = Path(tempfile.mkdtemp(prefix="cura-reslice-"))
    temp_output: Path | None = None
    log_path = source.with_name(source.stem + ".reslice.log")
    started = time.monotonic()
    try:
        env = build_environment(work, sources, args)
        # A temp output on the destination filesystem makes the final rename atomic.
        fd, name = tempfile.mkstemp(prefix=".cura-reslice-", suffix=".gcode.part", dir=source.parent)
        os.close(fd)
        temp_output = Path(name)
        job = {
            "job_id": uuid.uuid4().hex,
            "action": "slice", "printer": args.printer,
            "insert_before_end_path": str(args.insert_before_end) if args.insert_before_end else None,
            "input_path": str(source), "output_path": str(temp_output),
            "result_path": str(work / "result.json"),
            "timeout_seconds": args.timeout,
            "expected_version": CURA_VERSION,
        }
        emit(f"SLICE: {source}")
        if args.printer:
            emit(f"PRINTER: {args.printer}（変更先に保存された設定一式を使用）")
        if args.insert_before_end:
            emit(f"INSERT BEFORE END: {args.insert_before_end}")
        result = run_cura_job(job, work, log_path, cura, env, args)
        if not temp_output.is_file() or temp_output.stat().st_size == 0:
            raise ResliceError(f"出力G-codeが空です。ログ: {log_path}")
        publish_gcode(temp_output, target, args.overwrite)
        emit(f"OK: {target} ({target.stat().st_size:,} bytes / {time.monotonic() - started:.1f}秒)")
        return "success"
    finally:
        if temp_output is not None:
            temp_output.unlink(missing_ok=True)
        if args.keep_work:
            emit(f"WORK: {work}")
        else:
            shutil.rmtree(work, ignore_errors=True)


def main(argv: list[str] | None = None) -> int:
    # Japanese Windows consoles can otherwise fail before reporting the actual error.
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(errors="replace")
    arg_parser = parser()
    args = arg_parser.parse_args(argv)
    if args.timeout < 10 or not args.timeout < 604800:
        arg_parser.error("--timeout は10秒以上604800秒未満で指定してください。")
    if args.list_printers and (args.paths or args.printer or args.insert_before_end):
        arg_parser.error("--list-printers は入力ファイルや --printer と同時に指定できません。")
    if not args.list_printers and not args.paths:
        arg_parser.error("3MFファイルまたはフォルダを指定してください。プリンタの確認は --list-printers です。")
    if args.printer is not None and not args.printer.strip():
        arg_parser.error("--printer にプリンタ名またはIDを指定してください。")
    try:
        if not args.list_printers:
            args.insert_before_end = validate_insert_gcode(args.insert_before_end)
        cura = find_cura(args.cura)
        sources = profile_sources(args)
        if args.list_printers:
            return list_printers(cura, sources, args)
        inputs = collect_inputs(args.paths, args.recursive)
    except KeyboardInterrupt:
        emit("中断しました。")
        return 130
    except (ResliceError, OSError, ValueError, configparser.Error) as exc:
        emit(f"ERROR: {exc}")
        return 2
    emit(f"Cura 5.12.0 / 対象 {len(inputs)} 件")
    counts = {"success": 0, "skipped": 0, "failed": 0}
    for source in inputs:
        try:
            status = reslice_one(source, cura, sources, args)
            counts[status] += 1
        except KeyboardInterrupt:
            emit("中断しました。完了していないG-codeは保存していません。")
            return 130
        except (ResliceError, OSError, ValueError, configparser.Error) as exc:
            counts["failed"] += 1
            emit(f"ERROR: {source.name}: {exc}")
    emit(f"完了: 成功 {counts['success']} / スキップ {counts['skipped']} / 失敗 {counts['failed']}")
    return 1 if counts["failed"] else 0


if __name__ == "__main__":
    raise SystemExit(main())
