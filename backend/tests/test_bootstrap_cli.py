from types import SimpleNamespace

import pytest

from app.auth import bootstrap_admin


@pytest.fixture
def cli(monkeypatch):
    monkeypatch.setattr(
        "sys.argv",
        [
            "bootstrap",
            "--email",
            "admin@example.com",
            "--display-name",
            "Admin",
            "--department-slug",
            "hr",
        ],
    )
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: True))


def test_cli_refuses_piped_passwords(cli, monkeypatch):
    monkeypatch.setattr("sys.stdin", SimpleNamespace(isatty=lambda: False))

    def no_prompt(*args):
        pytest.fail("Non-interactive input must be rejected before reading a password")

    monkeypatch.setattr(bootstrap_admin.getpass, "getpass", no_prompt)
    with pytest.raises(SystemExit) as error:
        bootstrap_admin.main()
    assert error.value.code == 2


def test_cli_confirmation_mismatch_never_creates_account(cli, monkeypatch, capsys):
    values = iter(["first confidential password", "different confidential password"])
    monkeypatch.setattr(bootstrap_admin.getpass, "getpass", lambda _: next(values))

    async def forbidden(*args):
        pytest.fail("Mismatched passwords must not reach database setup")

    monkeypatch.setattr(bootstrap_admin, "create_first_admin", forbidden)
    with pytest.raises(SystemExit) as error:
        bootstrap_admin.main()
    assert error.value.code == 1
    assert "confidential" not in capsys.readouterr().err


def test_cli_does_not_echo_validation_exception(cli, monkeypatch, capsys):
    secret = "confidential test-only password"
    monkeypatch.setattr(bootstrap_admin.getpass, "getpass", lambda _: secret)

    async def invalid(*args):
        raise ValueError(secret)

    monkeypatch.setattr(bootstrap_admin, "create_first_admin", invalid)
    with pytest.raises(SystemExit) as error:
        bootstrap_admin.main()
    assert error.value.code == 1
    assert secret not in capsys.readouterr().err
