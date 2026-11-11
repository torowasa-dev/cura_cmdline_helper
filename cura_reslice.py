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

SCRIPT_VERSION = "1.1.1"
CURA_VERSION = "5.12.0"
PROFILE_VERSION = "5.12"
BRIDGE_NAME = "HeadlessResliceBridge"
# The release builder fills this with the matching, reviewable bridge source.
BRIDGE_ZIP_B64 = (
    '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'
    'AwADALgAAABRKgAAAAA='
)


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
            "input_path": str(source), "output_path": str(temp_output),
            "result_path": str(work / "result.json"),
            "timeout_seconds": args.timeout,
            "expected_version": CURA_VERSION,
        }
        emit(f"SLICE: {source}")
        if args.printer:
            emit(f"PRINTER: {args.printer}（変更先に保存された設定一式を使用）")
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
    if args.list_printers and (args.paths or args.printer):
        arg_parser.error("--list-printers は入力ファイルや --printer と同時に指定できません。")
    if not args.list_printers and not args.paths:
        arg_parser.error("3MFファイルまたはフォルダを指定してください。プリンタの確認は --list-printers です。")
    if args.printer is not None and not args.printer.strip():
        arg_parser.error("--printer にプリンタ名またはIDを指定してください。")
    try:
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
