"""macOSユーザープロファイル内へ分離して配置するlaunchd補助CLI。"""

from __future__ import annotations

import argparse
import json
import os
import plistlib
import re
import shutil
import socket
import stat
import subprocess
import sys
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from dotenv import dotenv_values

_PROFILE_PATTERN = re.compile(r"^[a-z0-9][a-z0-9-]{0,31}$")
_REQUIRED_ENV_KEYS = (
    "SHOPIFY_STORE_DOMAIN",
    "SHOPIFY_CLIENT_ID",
    "SHOPIFY_CLIENT_SECRET",
    "WAKOU_AUTH_USERNAME",
    "WAKOU_AUTH_PASSWORD",
)
_EXAMPLE_VALUES = {
    "your-store.myshopify.com",
    "your-client-id",
    "your-client-secret",
    "change-this-to-a-long-random-password",
}


def default_profile_root() -> Path:
    return Path.home() / "Library" / "Application Support" / "WakouCSV" / "profiles"


@dataclass(frozen=True)
class DeploymentProfile:
    profile: str
    app_dir: Path
    profile_root: Path
    port: int
    uv_bin: Path

    @classmethod
    def create(
        cls,
        *,
        profile: str,
        app_dir: Path,
        profile_root: Path,
        port: int,
        uv_bin: Path,
    ) -> DeploymentProfile:
        if not _PROFILE_PATTERN.fullmatch(profile):
            raise ValueError("profile must match [a-z0-9][a-z0-9-]{0,31}")
        if not 1024 <= port <= 65535:
            raise ValueError("port must be between 1024 and 65535")
        return cls(
            profile=profile,
            app_dir=app_dir.expanduser().resolve(),
            profile_root=Path(os.path.abspath(profile_root.expanduser())),
            port=port,
            uv_bin=uv_bin.expanduser().resolve(),
        )

    @property
    def profile_dir(self) -> Path:
        return self.profile_root / self.profile

    @property
    def label(self) -> str:
        return f"jp.co.wakou.csv-transfer.{self.profile}"

    @property
    def config_path(self) -> Path:
        return self.profile_dir / "profile.json"

    def as_config(self) -> dict[str, object]:
        return {
            "app_dir": str(self.app_dir),
            "port": self.port,
            "profile": self.profile,
            "uv_bin": str(self.uv_bin),
            "version": 1,
        }

    def launch_agent_plist(self) -> bytes:
        logs = self.profile_dir / "logs"
        payload: dict[str, Any] = {
            "Label": self.label,
            "ProgramArguments": [
                str(self.uv_bin),
                "run",
                "--project",
                str(self.app_dir),
                "uvicorn",
                "wakou_transfer.app:create_app",
                "--factory",
                "--host",
                "127.0.0.1",
                "--port",
                str(self.port),
                "--workers",
                "1",
            ],
            "WorkingDirectory": str(self.profile_dir),
            "EnvironmentVariables": {"PYTHONUNBUFFERED": "1"},
            "RunAtLoad": True,
            "KeepAlive": True,
            "ProcessType": "Background",
            "ThrottleInterval": 10,
            "StandardOutPath": str(logs / "app.log"),
            "StandardErrorPath": str(logs / "app-error.log"),
        }
        return plistlib.dumps(payload, sort_keys=True)


def prepare_profile(deployment: DeploymentProfile) -> None:
    if deployment.profile_dir.is_symlink():
        raise ValueError(f"profile path must not be a symbolic link: {deployment.profile_dir}")
    if deployment.profile_dir.exists() and not deployment.profile_dir.is_dir():
        raise ValueError(f"profile path is not a directory: {deployment.profile_dir}")
    deployment.profile_dir.mkdir(parents=True, exist_ok=True, mode=0o700)
    os.chmod(deployment.profile_dir, 0o700)
    for name in ("data", "logs"):
        path = deployment.profile_dir / name
        if path.is_symlink():
            raise ValueError(f"profile path must not be a symbolic link: {path}")
        if path.exists() and not path.is_dir():
            raise ValueError(f"profile path is not a directory: {path}")
        path.mkdir(exist_ok=True, mode=0o700)
        os.chmod(path, 0o700)

    env_path = deployment.profile_dir / ".env"
    if env_path.is_symlink():
        raise ValueError(f"profile path must not be a symbolic link: {env_path}")
    if not env_path.exists():
        example = deployment.app_dir / ".env.example"
        if not example.is_file():
            raise ValueError(f".env.example not found in app directory: {deployment.app_dir}")
        shutil.copyfile(example, env_path)
    os.chmod(env_path, 0o600)
    if deployment.config_path.is_symlink():
        raise ValueError(f"profile path must not be a symbolic link: {deployment.config_path}")
    deployment.config_path.write_text(
        json.dumps(deployment.as_config(), ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )
    os.chmod(deployment.config_path, 0o600)


def _read_env(path: Path) -> dict[str, str]:
    return {key: value or "" for key, value in dotenv_values(path).items()}


def _managed_path_errors(deployment: DeploymentProfile) -> list[str]:
    errors: list[str] = []
    if deployment.profile_root.is_symlink():
        return ["profile root must not be a symbolic link"]
    if deployment.profile_dir.is_symlink():
        return [f"{deployment.profile} must not be a symbolic link"]
    managed = (
        (deployment.profile_dir, "directory", 0o700),
        (deployment.profile_dir / "data", "directory", 0o700),
        (deployment.profile_dir / "logs", "directory", 0o700),
        (deployment.profile_dir / ".env", "file", 0o600),
        (deployment.config_path, "file", 0o600),
    )
    for path, expected_kind, expected_mode in managed:
        label = path.name or str(path)
        if path.is_symlink():
            errors.append(f"{label} must not be a symbolic link")
            continue
        if expected_kind == "directory" and not path.is_dir():
            errors.append(f"{label} directory is missing")
            continue
        if expected_kind == "file" and not path.is_file():
            errors.append(f"{label} file is missing")
            continue
        metadata = path.stat()
        if metadata.st_uid != os.getuid():
            errors.append(f"{label} must be owned by the current macOS user")
        actual_mode = stat.S_IMODE(metadata.st_mode)
        if actual_mode != expected_mode:
            errors.append(f"{label} permissions must be {expected_mode:o}")
    return errors


def validate_profile(deployment: DeploymentProfile) -> list[str]:
    errors = _managed_path_errors(deployment)
    if deployment.profile_root.is_symlink() or deployment.profile_dir.is_symlink():
        return errors
    if not (deployment.app_dir / "pyproject.toml").is_file():
        errors.append("app directory does not contain pyproject.toml")
    if not deployment.uv_bin.is_file() or not os.access(deployment.uv_bin, os.X_OK):
        errors.append("configured uv executable is missing or not executable")

    env_path = deployment.profile_dir / ".env"
    if env_path.is_symlink() or not env_path.is_file():
        return errors

    values = _read_env(env_path)
    for key in _REQUIRED_ENV_KEYS:
        value = values.get(key, "")
        if not value:
            errors.append(f"{key} is missing or empty")
        elif value in _EXAMPLE_VALUES or value.lower().startswith("your-"):
            errors.append(f"{key} still contains an example value")
    if values.get("WAKOU_AUTH_PASSWORD") and len(values["WAKOU_AUTH_PASSWORD"]) < 16:
        errors.append("WAKOU_AUTH_PASSWORD must be at least 16 characters")
    return errors


def load_profile(profile: str, profile_root: Path) -> DeploymentProfile:
    if not _PROFILE_PATTERN.fullmatch(profile):
        raise ValueError("profile must match [a-z0-9][a-z0-9-]{0,31}")
    root = Path(os.path.abspath(profile_root.expanduser()))
    profile_dir = root / profile
    config_path = profile_dir / "profile.json"
    if profile_dir.is_symlink() or config_path.is_symlink():
        raise ValueError("profile paths must not be symbolic links")
    if not config_path.is_file():
        raise ValueError(f"profile is not prepared: {profile}")
    data = json.loads(config_path.read_text(encoding="utf-8"))
    if data.get("version") != 1 or data.get("profile") != profile:
        raise ValueError("unsupported or mismatched profile configuration")
    return DeploymentProfile.create(
        profile=profile,
        app_dir=Path(str(data["app_dir"])),
        profile_root=root,
        port=int(data["port"]),
        uv_bin=Path(str(data["uv_bin"])),
    )


def _launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["launchctl", *args],
        check=check,
        capture_output=True,
        text=True,
    )


def _job_result(deployment: DeploymentProfile) -> subprocess.CompletedProcess[str]:
    target = f"gui/{os.getuid()}/{deployment.label}"
    return _launchctl("print", target, check=False)


def _job_running(deployment: DeploymentProfile) -> bool:
    result = _job_result(deployment)
    if result.returncode != 0:
        return False
    return "state = running" in result.stdout or bool(re.search(r"\bpid = \d+", result.stdout))


def _job_loaded(deployment: DeploymentProfile) -> bool:
    return _job_result(deployment).returncode == 0


def _port_available(port: int) -> bool:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
        try:
            probe.bind(("127.0.0.1", port))
        except OSError:
            return False
    return True


def runtime_validation_errors(
    deployment: DeploymentProfile,
    *,
    check_bound_port: bool = True,
    own_job_running: bool | None = None,
) -> list[str]:
    errors: list[str] = []
    if deployment.profile_root.is_dir():
        for candidate in deployment.profile_root.iterdir():
            if (
                candidate.name == deployment.profile
                or not candidate.is_dir()
                or candidate.is_symlink()
            ):
                continue
            config_path = candidate / "profile.json"
            if not config_path.is_file() or config_path.is_symlink():
                continue
            try:
                data = json.loads(config_path.read_text(encoding="utf-8"))
                candidate_port = int(data["port"])
            except (KeyError, TypeError, ValueError, json.JSONDecodeError):
                continue
            if candidate_port == deployment.port:
                errors.append(
                    f"port {deployment.port} is also configured by profile {candidate.name}"
                )
    if check_bound_port:
        running = _job_running(deployment) if own_job_running is None else own_job_running
        if not running and not _port_available(deployment.port):
            errors.append(f"port {deployment.port} is already in use")
    return errors


def _health_ok(deployment: DeploymentProfile) -> bool:
    try:
        with urllib.request.urlopen(  # noqa: S310 -- fixed localhost URL
            f"http://127.0.0.1:{deployment.port}/api/health", timeout=3
        ) as response:
            if response.status != 200:
                return False
            payload = json.loads(response.read())
            return bool(
                isinstance(payload, dict)
                and payload.get("service") == "wakou-transfer"
                and payload.get("status") == "ok"
            )
    except (OSError, urllib.error.URLError, json.JSONDecodeError):
        return False


def _wait_for_health(deployment: DeploymentProfile, timeout: float = 15.0) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if _job_running(deployment) and _health_ok(deployment):
            return True
        time.sleep(0.2)
    return False


def _atomic_write(path: Path, content: bytes) -> None:
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_bytes(content)
        os.chmod(temporary, 0o600)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _ensure_launch_agents_directory(path: Path) -> None:
    if path.is_symlink():
        raise ValueError(f"LaunchAgents directory must not be a symbolic link: {path}")
    if path.exists() and not path.is_dir():
        raise ValueError(f"LaunchAgents path is not a directory: {path}")
    path.mkdir(parents=True, exist_ok=True)
    if path.stat().st_uid != os.getuid():
        raise ValueError("LaunchAgents directory must be owned by the current macOS user")


def install_profile(deployment: DeploymentProfile, launch_agents_dir: Path) -> Path:
    errors = [*validate_profile(deployment), *runtime_validation_errors(deployment)]
    if errors:
        raise ValueError("profile validation failed:\n- " + "\n- ".join(errors))
    _ensure_launch_agents_directory(launch_agents_dir)
    plist_path = launch_agents_dir / f"{deployment.label}.plist"
    if plist_path.is_symlink():
        raise ValueError(f"launch agent path must not be a symbolic link: {plist_path}")
    new_plist = deployment.launch_agent_plist()
    plistlib.loads(new_plist)
    old_plist = plist_path.read_bytes() if plist_path.is_file() else None
    was_loaded = _job_loaded(deployment)
    domain = f"gui/{os.getuid()}"
    target = f"{domain}/{deployment.label}"

    if was_loaded:
        _launchctl("bootout", target)
    _atomic_write(plist_path, new_plist)
    try:
        _launchctl("bootstrap", domain, str(plist_path))
        _launchctl("kickstart", "-k", target)
        if not _wait_for_health(deployment):
            raise RuntimeError("launch agent started but the Wakou health check did not pass")
    except (OSError, RuntimeError, subprocess.CalledProcessError):
        _launchctl("bootout", target, check=False)
        if old_plist is None:
            plist_path.unlink(missing_ok=True)
        else:
            _atomic_write(plist_path, old_plist)
            if was_loaded:
                _launchctl("bootstrap", domain, str(plist_path))
                _launchctl("kickstart", "-k", target)
        raise
    return plist_path


def uninstall_profile(deployment: DeploymentProfile, launch_agents_dir: Path) -> None:
    _ensure_launch_agents_directory(launch_agents_dir)
    target = f"gui/{os.getuid()}/{deployment.label}"
    if _job_loaded(deployment):
        _launchctl("bootout", target)
    plist_path = launch_agents_dir / f"{deployment.label}.plist"
    if plist_path.is_symlink():
        raise ValueError(f"launch agent path must not be a symbolic link: {plist_path}")
    plist_path.unlink(missing_ok=True)


def profile_status(deployment: DeploymentProfile) -> tuple[bool, bool]:
    running = _job_running(deployment)
    return running, running and _health_ok(deployment)


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wakou-macos-profile",
        description="Mac miniのユーザープロファイル内へワコウCSVツールを分離配置します。",
    )
    parser.add_argument("--profile-root", type=Path, default=default_profile_root())
    subparsers = parser.add_subparsers(dest="command", required=True)

    prepare = subparsers.add_parser("prepare", help="非秘密設定と専用ディレクトリを作成")
    prepare.add_argument("--profile", required=True)
    prepare.add_argument("--app-dir", required=True, type=Path)
    prepare.add_argument("--port", required=True, type=int)
    prepare.add_argument("--uv-bin", type=Path, default=None)

    for command in ("validate", "install", "status", "uninstall"):
        subparser = subparsers.add_parser(command)
        subparser.add_argument("--profile", required=True)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = _parser().parse_args(argv)
    try:
        if args.command == "prepare":
            uv_bin = args.uv_bin or Path(shutil.which("uv") or "uv")
            deployment = DeploymentProfile.create(
                profile=args.profile,
                app_dir=args.app_dir,
                profile_root=args.profile_root,
                port=args.port,
                uv_bin=uv_bin,
            )
            prepare_profile(deployment)
            print(f"prepared_profile={deployment.profile}")
            print(f"profile_dir={deployment.profile_dir}")
            print(f"env_file={deployment.profile_dir / '.env'}")
            print("next=edit .env without sharing secret values, then run validate")
            return 0

        deployment = load_profile(args.profile, args.profile_root)
        launch_agents_dir = Path.home() / "Library" / "LaunchAgents"
        if args.command == "validate":
            errors = [*validate_profile(deployment), *runtime_validation_errors(deployment)]
            if errors:
                for error in errors:
                    print(f"ERROR: {error}", file=sys.stderr)
                return 1
            print("profile_validation=ok")
            return 0
        if args.command == "install":
            plist_path = install_profile(deployment, launch_agents_dir)
            print(f"launch_agent_installed={plist_path}")
            print(f"local_url=http://127.0.0.1:{deployment.port}")
            return 0
        if args.command == "status":
            loaded, healthy = profile_status(deployment)
            print(f"launch_agent_loaded={str(loaded).lower()}")
            print(f"health={str(healthy).lower()}")
            return 0 if loaded and healthy else 1
        if args.command == "uninstall":
            uninstall_profile(deployment, launch_agents_dir)
            print("launch_agent_removed=true")
            print("profile_data_preserved=true")
            return 0
    except (
        OSError,
        RuntimeError,
        ValueError,
        json.JSONDecodeError,
        subprocess.CalledProcessError,
    ) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    return 1


if __name__ == "__main__":
    raise SystemExit(main())
