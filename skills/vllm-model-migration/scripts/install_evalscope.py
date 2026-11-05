#!/usr/bin/env python3
"""Install EvalScope with its performance dependencies into a selected Python environment."""
import argparse
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys


PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple"

REQUIRED_PERF_OPTIONS = (
    "--stream", "--api", "--dataset", "--url", "--tokenizer-path", "--parallel", "--number",
    "--warmup-num", "--min-prompt-length", "--max-prompt-length", "--min-tokens",
    "--max-tokens", "--seed", "--temperature", "--extra-args", "--outputs-dir",
    "--no-test-connection",
)


def resolve_python(value):
    candidate = Path(value).expanduser()
    if candidate.is_file():
        return os.path.abspath(str(candidate))
    executable = shutil.which(value)
    if executable:
        return executable
    raise ValueError("Python executable not found: " + value)


def venv_python(venv_dir):
    directory = Path(venv_dir).expanduser().resolve()
    relative = Path("Scripts/python.exe") if os.name == "nt" else Path("bin/python")
    return directory, directory / relative


def prepare_python(python, venv_dir=None, dry_run=False):
    base_python = resolve_python(python)
    if not venv_dir:
        return base_python, None

    directory, target_python = venv_python(venv_dir)
    if target_python.is_file():
        return str(target_python), directory
    if dry_run:
        return str(target_python), directory
    directory.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run([base_python, "-m", "venv", str(directory)], check=False)
    if result.returncode:
        raise RuntimeError("Creating virtual environment failed with exit code {}".format(result.returncode))
    if not target_python.is_file():
        raise RuntimeError("Virtual environment Python was not created: " + str(target_python))
    return str(target_python), directory


def build_install_command(python, version=None, upgrade=False):
    package = "evalscope[perf]"
    if version:
        package += "==" + version
    command = [python, "-m", "pip", "install", "--disable-pip-version-check",
               "--index-url", PIP_INDEX_URL]
    if upgrade:
        command.append("--upgrade")
    command.append(package)
    return command


def run_text_command(command, label):
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode:
        detail = result.stderr.strip() or result.stdout.strip()
        raise RuntimeError("{} failed{}".format(label, ": " + detail if detail else ""))
    return result.stdout.strip()


def check_evalscope_cli(executable):
    if not Path(executable).is_file():
        raise RuntimeError("EvalScope executable not found: " + str(executable))
    try:
        result = subprocess.run(
            [str(executable), "perf", "--help"], capture_output=True, text=True, timeout=30, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise RuntimeError("Running EvalScope CLI help failed: " + str(exc)) from exc
    if result.returncode or any(option not in result.stdout for option in REQUIRED_PERF_OPTIONS):
        raise RuntimeError("Installed EvalScope does not expose required perf options; check evalscope perf --help")


def install(python, venv_dir=None, version=None, upgrade=False, dry_run=False):
    python, venv_path = prepare_python(python, venv_dir, dry_run)
    command = build_install_command(python, version, upgrade)
    if dry_run:
        result = {"status": "planned", "python": python, "command": command}
        if venv_path:
            result["venv"] = str(venv_path)
        return result

    result = subprocess.run(command, check=False)
    if result.returncode:
        raise RuntimeError("pip install failed with exit code {}".format(result.returncode))

    scripts_dir = run_text_command(
        [python, "-c", "import sysconfig; print(sysconfig.get_path('scripts'))"],
        "Reading Python scripts directory",
    )
    candidates = [Path(scripts_dir) / name for name in ("evalscope", "evalscope.exe")]
    executable = next((path for path in candidates if path.is_file()), None)
    if executable is None:
        raise RuntimeError("EvalScope CLI was not found in the target Python environment: " + scripts_dir)

    check_evalscope_cli(str(executable))
    installed_version = run_text_command(
        [python, "-c", "from importlib.metadata import version; print(version('evalscope'))"],
        "Reading EvalScope version",
    )
    result = {
        "status": "success",
        "python": python,
        "evalscope_executable": str(executable),
        "evalscope_version": installed_version,
        "package": command[-1],
    }
    if venv_path:
        result["venv"] = str(venv_path)
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--python", default=sys.executable,
                        help="base Python executable (default: current interpreter); used directly unless --venv is set")
    parser.add_argument("--venv",
                        help="create or reuse this virtual environment and install EvalScope into it")
    parser.add_argument("--version", help="optional EvalScope version to install")
    parser.add_argument("--upgrade", action="store_true",
                        help="upgrade EvalScope and its performance dependencies")
    parser.add_argument("--dry-run", action="store_true", help="print the install command without running it")
    args = parser.parse_args(argv)
    try:
        result = install(args.python, args.venv, args.version, args.upgrade, args.dry_run)
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "failed", "error": str(exc)}, ensure_ascii=False), file=sys.stderr)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
