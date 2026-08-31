"""Tests de la sauvegarde PostgreSQL (dump_database) — TECH-009."""

import os

import pytest

from core.backup.database import DatabaseBackupError, DatabaseConnectionParams, dump_database


def _real_params(**overrides) -> DatabaseConnectionParams:
    base = dict(
        name=os.environ["DB_NAME"],
        user=os.environ["DB_USER"],
        password=os.environ["DB_PASSWORD"],
        host=os.environ["DB_HOST"],
        port=os.environ["DB_PORT"],
    )
    base.update(overrides)
    return DatabaseConnectionParams(**base)


class FakeRunner:
    """Capture la commande exacte transmise, sans exécuter de processus réel."""

    def __init__(self, returncode=0, stderr=""):
        self.returncode = returncode
        self.stderr = stderr
        self.calls = []

    def __call__(self, command, **kwargs):
        self.calls.append({"command": command, "kwargs": kwargs})

        class Result:
            pass

        result = Result()
        result.returncode = self.returncode
        result.stderr = self.stderr
        result.stdout = ""
        return result


class TestCommandConstruction:
    def test_command_is_built_as_a_list_never_a_shell_string(self, tmp_path):
        runner = FakeRunner()
        dump_database(_real_params(), tmp_path / "out.dump", runner=runner)

        call = runner.calls[0]
        assert isinstance(call["command"], list)
        assert call["kwargs"].get("check") is False or "check" not in call["kwargs"]
        # Aucune trace de shell=True nulle part.
        assert "shell" not in call["kwargs"] or call["kwargs"]["shell"] is False

    def test_password_never_appears_in_command_arguments(self, tmp_path):
        runner = FakeRunner()
        params = _real_params(password="s3cr3t-p@ss")
        dump_database(params, tmp_path / "out.dump", runner=runner)

        command = runner.calls[0]["command"]
        assert "s3cr3t-p@ss" not in command
        assert all("s3cr3t-p@ss" not in str(arg) for arg in command)

    def test_password_is_transmitted_via_pgpassword_env_var(self, tmp_path):
        runner = FakeRunner()
        params = _real_params(password="s3cr3t-p@ss")
        dump_database(params, tmp_path / "out.dump", runner=runner)

        env = runner.calls[0]["kwargs"]["env"]
        assert env["PGPASSWORD"] == "s3cr3t-p@ss"

    def test_shell_metacharacters_in_db_name_are_passed_as_a_single_argument(self, tmp_path):
        """Aucune injection possible : un nom contenant des métacaractères
        shell reste un simple élément de liste, jamais interprété."""
        runner = FakeRunner()
        malicious_name = "corrux; rm -rf / #"
        dump_database(_real_params(name=malicious_name), tmp_path / "out.dump", runner=runner)

        command = runner.calls[0]["command"]
        assert malicious_name in command  # présent tel quel, un seul élément
        assert command.count(malicious_name) == 1


@pytest.mark.django_db
class TestRealDatabaseDump:
    def test_successful_dump_against_real_postgres(self, tmp_path):
        destination = tmp_path / "reussi.dump"
        result = dump_database(_real_params(), destination)

        assert result == destination
        assert destination.exists()
        assert destination.stat().st_size > 0

    def test_dump_fails_with_wrong_credentials(self, tmp_path):
        destination = tmp_path / "echoue.dump"
        bad_params = _real_params(password="mot-de-passe-incorrect")

        with pytest.raises(DatabaseBackupError):
            dump_database(bad_params, destination)

    def test_failed_dump_error_message_never_contains_the_password(self, tmp_path):
        destination = tmp_path / "echoue.dump"
        bad_params = _real_params(password="mot-de-passe-tres-secret")

        with pytest.raises(DatabaseBackupError) as exc_info:
            dump_database(bad_params, destination)

        assert "mot-de-passe-tres-secret" not in str(exc_info.value)
