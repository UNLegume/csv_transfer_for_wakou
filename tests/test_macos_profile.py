from __future__ import annotations

import json
import os
import plistlib
import socket
import subprocess
from pathlib import Path

import pytest

from wakou_transfer.macos_profile import (
    DeploymentProfile,
    install_profile,
    prepare_profile,
    profile_status,
    runtime_validation_errors,
    uninstall_profile,
    validate_profile,
)


def make_app(tmp_path: Path) -> Path:
    app_dir = tmp_path / "app"
    app_dir.mkdir()
    (app_dir / "pyproject.toml").write_text("[project]\nname='test'\n", encoding="utf-8")
    (app_dir / ".env.example").write_text(
        "SHOPIFY_STORE_DOMAIN=your-store.myshopify.com\n"
        "SHOPIFY_CLIENT_ID=your-client-id\n"
        "SHOPIFY_CLIENT_SECRET=your-client-secret\n"
        "WAKOU_AUTH_USERNAME=operator\n"
        "WAKOU_AUTH_PASSWORD=change-this-to-a-long-random-password\n",
        encoding="utf-8",
    )
    return app_dir


def configured_deployment(tmp_path: Path) -> DeploymentProfile:
    deployment = DeploymentProfile.create(
        profile="production",
        app_dir=make_app(tmp_path),
        profile_root=tmp_path / "profiles",
        port=8100,
        uv_bin=Path("/bin/sh"),
    )
    prepare_profile(deployment)
    (deployment.profile_dir / ".env").write_text(
        "SHOPIFY_STORE_DOMAIN=store.myshopify.com\n"
        "SHOPIFY_CLIENT_ID=client-id\n"
        "SHOPIFY_CLIENT_SECRET=secret-value\n"
        "WAKOU_AUTH_USERNAME=operator\n"
        "WAKOU_AUTH_PASSWORD=a-long-random-password\n",
        encoding="utf-8",
    )
    os.chmod(deployment.profile_dir / ".env", 0o600)
    return deployment


def test_profile_rejects_unsafe_names(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="profile"):
        DeploymentProfile.create(
            profile="../production",
            app_dir=make_app(tmp_path),
            profile_root=tmp_path / "profiles",
            port=8100,
            uv_bin=Path("/opt/homebrew/bin/uv"),
        )


def test_prepare_creates_isolated_runtime_without_overwriting_env(tmp_path: Path) -> None:
    app_dir = make_app(tmp_path)
    deployment = DeploymentProfile.create(
        profile="production",
        app_dir=app_dir,
        profile_root=tmp_path / "profiles",
        port=8100,
        uv_bin=Path("/opt/homebrew/bin/uv"),
    )

    prepare_profile(deployment)

    assert deployment.profile_dir == tmp_path / "profiles" / "production"
    assert (deployment.profile_dir / "data").is_dir()
    assert (deployment.profile_dir / "logs").is_dir()
    env_path = deployment.profile_dir / ".env"
    assert env_path.read_text(encoding="utf-8").startswith("SHOPIFY_STORE_DOMAIN=")
    assert os.stat(env_path).st_mode & 0o777 == 0o600
    config = json.loads((deployment.profile_dir / "profile.json").read_text(encoding="utf-8"))
    assert config == {
        "app_dir": str(app_dir.resolve()),
        "port": 8100,
        "profile": "production",
        "uv_bin": "/opt/homebrew/bin/uv",
        "version": 1,
    }

    env_path.write_text("KEEP=existing\n", encoding="utf-8")
    prepare_profile(deployment)
    assert env_path.read_text(encoding="utf-8") == "KEEP=existing\n"


def test_launch_agent_is_scoped_to_profile_and_localhost(tmp_path: Path) -> None:
    deployment = DeploymentProfile.create(
        profile="production",
        app_dir=make_app(tmp_path),
        profile_root=tmp_path / "profiles",
        port=8100,
        uv_bin=Path("/opt/homebrew/bin/uv"),
    )

    plist = plistlib.loads(deployment.launch_agent_plist())

    assert plist["Label"] == "jp.co.wakou.csv-transfer.production"
    assert plist["WorkingDirectory"] == str(deployment.profile_dir)
    assert plist["ProgramArguments"] == [
        "/opt/homebrew/bin/uv",
        "run",
        "--project",
        str(deployment.app_dir),
        "uvicorn",
        "wakou_transfer.app:create_app",
        "--factory",
        "--host",
        "127.0.0.1",
        "--port",
        "8100",
        "--workers",
        "1",
    ]
    assert plist["RunAtLoad"] is True
    assert plist["KeepAlive"] is True
    assert plist["StandardOutPath"] == str(deployment.profile_dir / "logs" / "app.log")
    assert plist["StandardErrorPath"] == str(deployment.profile_dir / "logs" / "app-error.log")


def test_launch_agent_preserves_paths_with_spaces(tmp_path: Path) -> None:
    spaced = tmp_path / "directory with spaces"
    spaced.mkdir()
    deployment = DeploymentProfile.create(
        profile="production",
        app_dir=make_app(spaced),
        profile_root=spaced / "profile root",
        port=8100,
        uv_bin=Path("/bin/sh"),
    )

    plist = plistlib.loads(deployment.launch_agent_plist())

    assert plist["ProgramArguments"][3] == str(deployment.app_dir)
    assert plist["WorkingDirectory"] == str(deployment.profile_dir)


def test_validate_reports_placeholders_without_echoing_secret_values(tmp_path: Path) -> None:
    deployment = DeploymentProfile.create(
        profile="production",
        app_dir=make_app(tmp_path),
        profile_root=tmp_path / "profiles",
        port=8100,
        uv_bin=Path("/opt/homebrew/bin/uv"),
    )
    prepare_profile(deployment)

    errors = validate_profile(deployment)

    assert any("SHOPIFY_STORE_DOMAIN" in error for error in errors)
    assert any("SHOPIFY_CLIENT_SECRET" in error for error in errors)
    assert all("your-client-secret" not in error for error in errors)


def test_validate_accepts_profile_without_sender_or_contract_codes(tmp_path: Path) -> None:
    deployment = DeploymentProfile.create(
        profile="production",
        app_dir=make_app(tmp_path),
        profile_root=tmp_path / "profiles",
        port=8100,
        uv_bin=Path("/bin/sh"),
    )
    prepare_profile(deployment)
    (deployment.profile_dir / ".env").write_text(
        "SHOPIFY_STORE_DOMAIN=store.myshopify.com\n"
        "SHOPIFY_CLIENT_ID=client-id\n"
        "SHOPIFY_CLIENT_SECRET=secret-value\n"
        "WAKOU_AUTH_USERNAME=operator\n"
        "WAKOU_AUTH_PASSWORD=a-long-random-password\n",
        encoding="utf-8",
    )
    os.chmod(deployment.profile_dir / ".env", 0o600)

    assert validate_profile(deployment) == []


def test_validate_parses_quoted_empty_and_example_values(tmp_path: Path) -> None:
    deployment = DeploymentProfile.create(
        profile="production",
        app_dir=make_app(tmp_path),
        profile_root=tmp_path / "profiles",
        port=8100,
        uv_bin=Path("/bin/sh"),
    )
    prepare_profile(deployment)
    (deployment.profile_dir / ".env").write_text(
        'SHOPIFY_STORE_DOMAIN="your-store.myshopify.com"\n'
        'SHOPIFY_CLIENT_ID=""\n'
        'SHOPIFY_CLIENT_SECRET="your-client-secret"\n',
        encoding="utf-8",
    )
    os.chmod(deployment.profile_dir / ".env", 0o600)

    errors = validate_profile(deployment)

    assert any("SHOPIFY_STORE_DOMAIN" in error and "example" in error for error in errors)
    assert any("SHOPIFY_CLIENT_ID" in error and "empty" in error for error in errors)
    assert any("SHOPIFY_CLIENT_SECRET" in error and "example" in error for error in errors)


def test_prepare_rejects_symlinked_profile_paths(tmp_path: Path) -> None:
    app_dir = make_app(tmp_path)
    profile_root = tmp_path / "profiles"
    profile_root.mkdir()
    target = tmp_path / "outside"
    target.mkdir()
    (profile_root / "production").symlink_to(target, target_is_directory=True)
    deployment = DeploymentProfile.create(
        profile="production",
        app_dir=app_dir,
        profile_root=profile_root,
        port=8100,
        uv_bin=Path("/bin/sh"),
    )

    with pytest.raises(ValueError, match="symbolic link"):
        prepare_profile(deployment)


@pytest.mark.parametrize("relative_path", [".env", "profile.json", "data", "logs"])
def test_validate_rejects_managed_path_replaced_by_symlink(
    tmp_path: Path, relative_path: str
) -> None:
    deployment = configured_deployment(tmp_path)
    managed = deployment.profile_dir / relative_path
    outside = tmp_path / f"outside-{relative_path.replace('.', 'file')}"
    if managed.is_dir():
        managed.rmdir()
        outside.mkdir()
        managed.symlink_to(outside, target_is_directory=True)
    else:
        managed.unlink()
        outside.write_text("not trusted", encoding="utf-8")
        managed.symlink_to(outside)

    errors = validate_profile(deployment)

    assert any("symbolic link" in error and relative_path in error for error in errors)


def test_validate_rejects_insecure_runtime_directory_permissions(tmp_path: Path) -> None:
    deployment = configured_deployment(tmp_path)
    os.chmod(deployment.profile_dir / "data", 0o755)

    errors = validate_profile(deployment)

    assert any("data" in error and "700" in error for error in errors)


def test_validate_does_not_follow_replaced_profile_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deployment = configured_deployment(tmp_path)
    original = deployment.profile_dir.with_name("production-original")
    deployment.profile_dir.rename(original)
    outside = tmp_path / "outside-profile"
    outside.mkdir(mode=0o700)
    (outside / "data").mkdir(mode=0o700)
    (outside / "logs").mkdir(mode=0o700)
    (outside / ".env").write_text("SHOPIFY_CLIENT_SECRET=outside\n", encoding="utf-8")
    (outside / "profile.json").write_text("{}\n", encoding="utf-8")
    os.chmod(outside / ".env", 0o600)
    os.chmod(outside / "profile.json", 0o600)
    deployment.profile_dir.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(
        "wakou_transfer.macos_profile.dotenv_values",
        lambda *_: pytest.fail("symlinked .env must not be parsed"),
    )

    errors = validate_profile(deployment)

    assert any("symbolic link" in error for error in errors)


def test_runtime_validation_rejects_other_profile_using_same_port(tmp_path: Path) -> None:
    app_dir = make_app(tmp_path)
    root = tmp_path / "profiles"
    first = DeploymentProfile.create(
        profile="production",
        app_dir=app_dir,
        profile_root=root,
        port=8100,
        uv_bin=Path("/bin/sh"),
    )
    second = DeploymentProfile.create(
        profile="staging",
        app_dir=app_dir,
        profile_root=root,
        port=8100,
        uv_bin=Path("/bin/sh"),
    )
    prepare_profile(first)
    prepare_profile(second)

    errors = runtime_validation_errors(first, check_bound_port=False)

    assert any("staging" in error and "8100" in error for error in errors)


def test_runtime_validation_rejects_bound_port(tmp_path: Path) -> None:
    app_dir = make_app(tmp_path)
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        port = int(listener.getsockname()[1])
        deployment = DeploymentProfile.create(
            profile="production",
            app_dir=app_dir,
            profile_root=tmp_path / "profiles",
            port=port,
            uv_bin=Path("/bin/sh"),
        )
        prepare_profile(deployment)

        errors = runtime_validation_errors(deployment, own_job_running=False)

    assert any("already in use" in error for error in errors)


def test_install_restores_previous_plist_when_bootstrap_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deployment = configured_deployment(tmp_path)
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    plist_path = agents / f"{deployment.label}.plist"
    old_plist = b"old-plist"
    plist_path.write_bytes(old_plist)
    calls: list[tuple[str, ...]] = []
    bootstrap_attempts = 0

    def fake_launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        nonlocal bootstrap_attempts
        calls.append(args)
        if args[0] == "print":
            return subprocess.CompletedProcess(args, 0, "state = running\npid = 123\n", "")
        if args[0] == "bootstrap":
            bootstrap_attempts += 1
            if bootstrap_attempts == 1:
                raise subprocess.CalledProcessError(5, args)
        return subprocess.CompletedProcess(args, 0, "", "")

    monkeypatch.setattr("wakou_transfer.macos_profile._launchctl", fake_launchctl)
    monkeypatch.setattr("wakou_transfer.macos_profile._wait_for_health", lambda _: True)

    with pytest.raises(subprocess.CalledProcessError):
        install_profile(deployment, agents)

    assert plist_path.read_bytes() == old_plist
    assert bootstrap_attempts == 2
    assert any(call[0] == "bootout" for call in calls)


def test_install_rejects_symlinked_launch_agents_directory(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deployment = configured_deployment(tmp_path)
    outside = tmp_path / "outside-agents"
    outside.mkdir()
    agents = tmp_path / "LaunchAgents"
    agents.symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(
        "wakou_transfer.macos_profile._launchctl",
        lambda *args, **kwargs: subprocess.CompletedProcess(args, 1, "", ""),
    )

    with pytest.raises(ValueError, match="LaunchAgents.*symbolic link"):
        install_profile(deployment, agents)


def test_uninstall_keeps_plist_when_bootout_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deployment = configured_deployment(tmp_path)
    agents = tmp_path / "LaunchAgents"
    agents.mkdir()
    plist_path = agents / f"{deployment.label}.plist"
    plist_path.write_bytes(deployment.launch_agent_plist())

    def fake_launchctl(*args: str, check: bool = True) -> subprocess.CompletedProcess[str]:
        if args[0] == "print":
            return subprocess.CompletedProcess(args, 0, "state = running\npid = 123\n", "")
        raise subprocess.CalledProcessError(5, args)

    monkeypatch.setattr("wakou_transfer.macos_profile._launchctl", fake_launchctl)

    with pytest.raises(subprocess.CalledProcessError):
        uninstall_profile(deployment, agents)

    assert plist_path.exists()


def test_status_rejects_unrelated_health_endpoint(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    deployment = configured_deployment(tmp_path)

    class Response:
        status = 200

        def __enter__(self) -> Response:
            return self

        def __exit__(self, *_: object) -> None:
            return None

        def read(self) -> bytes:
            return b'{"status":"ok"}'

    monkeypatch.setattr(
        "wakou_transfer.macos_profile._launchctl",
        lambda *args, **kwargs: subprocess.CompletedProcess(
            args, 0, "state = running\npid = 123\n", ""
        ),
    )
    monkeypatch.setattr("urllib.request.urlopen", lambda *args, **kwargs: Response())

    assert profile_status(deployment) == (True, False)


def test_validate_accepts_configured_profile(tmp_path: Path) -> None:
    deployment = DeploymentProfile.create(
        profile="production",
        app_dir=make_app(tmp_path),
        profile_root=tmp_path / "profiles",
        port=8100,
        uv_bin=Path("/bin/sh"),
    )
    prepare_profile(deployment)
    (deployment.profile_dir / ".env").write_text(
        "SHOPIFY_STORE_DOMAIN=store.myshopify.com\n"
        "SHOPIFY_CLIENT_ID=client-id\n"
        "SHOPIFY_CLIENT_SECRET=secret-value\n"
        "WAKOU_AUTH_USERNAME=operator\n"
        "WAKOU_AUTH_PASSWORD=a-long-random-password\n",
        encoding="utf-8",
    )
    os.chmod(deployment.profile_dir / ".env", 0o600)

    assert validate_profile(deployment) == []
