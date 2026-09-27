#!/usr/bin/env python3
"""Simple setup helper to create a virtualenv and install requirements.

Run from the project root (one level above this folder):

    python3 customer_service_agent_demo/setup_env.py

This will create a `.venv` directory, upgrade pip inside it, and install
packages from `customer_service_agent_demo/requirements.txt`.
"""
from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
import venv
from pathlib import Path


def run(cmd: list[str], **kwargs) -> None:
    print("$", " ".join(cmd))
    subprocess.run(cmd, check=True, **kwargs)


def create_venv(venv_path: Path, clear: bool = False) -> None:
    if venv_path.exists() and clear:
        print(f"Removing existing venv at {venv_path}")
        shutil.rmtree(venv_path)
    if not venv_path.exists():
        print(f"Creating virtual environment at {venv_path}")
        venv.EnvBuilder(with_pip=True).create(str(venv_path))
    else:
        print(f"Using existing virtual environment at {venv_path}")


def install_requirements(python_exe: str, requirements: Path, upgrade_pip: bool) -> None:
    if upgrade_pip:
        run([python_exe, "-m", "pip", "install", "--upgrade", "pip"])    
    if requirements.exists():
        run([python_exe, "-m", "pip", "install", "-r", str(requirements)])
    else:
        print(f"No requirements file found at {requirements}; skipping install.")


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--venv-path", default=".venv", help="Path to create the virtualenv")
    parser.add_argument("--no-venv", action="store_true", help="Install into the current Python instead of creating a venv")
    parser.add_argument("--clear", action="store_true", help="Remove existing venv before creating")
    parser.add_argument("--no-upgrade-pip", dest="upgrade_pip", action="store_false", help="Do not upgrade pip inside the venv")
    parser.add_argument("--requirements", default="customer_service_agent_demo/requirements.txt")
    args = parser.parse_args()

    req_path = Path(args.requirements)
    if args.no_venv:
        print("Installing into the current Python interpreter:", sys.executable)
        try:
            install_requirements(sys.executable, req_path, args.upgrade_pip)
        except subprocess.CalledProcessError as exc:
            print("Installation failed:", exc)
            raise SystemExit(1)
        print("Done.")
        return

    venv_path = Path(args.venv_path)
    try:
        create_venv(venv_path, clear=args.clear)
    except Exception as exc:
        print("Failed to create virtualenv:", exc)
        raise SystemExit(1)

    python_exe = venv_path / "bin" / "python"
    if not python_exe.exists():
        print("Could not find python executable inside the venv at", python_exe)
        raise SystemExit(1)

    try:
        install_requirements(str(python_exe), req_path, args.upgrade_pip)
    except subprocess.CalledProcessError as exc:
        print("Installation failed:", exc)
        raise SystemExit(1)

    print()
    print("Setup complete.")
    print("To use the virtualenv, run:")
    print(f"  source {venv_path}/bin/activate")
    print("or run commands with the venv python:")
    print(f"  {venv_path}/bin/python customer_service_agent_demo/customer_service_agent_langgraph.py --reset-db")


if __name__ == "__main__":
    main()
