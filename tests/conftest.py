import os
import socket
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import pytest
from sqlalchemy import create_engine


ROOT = Path(__file__).resolve().parents[1]


@dataclass(frozen=True)
class ComposeStack:
    project: str
    environment: dict[str, str] = field(repr=False)

    def run(self, *args: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [
                "docker",
                "compose",
                "-p",
                self.project,
                "--profile",
                "app",
                *args,
            ],
            cwd=ROOT,
            env=self.environment,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            check=False,
        )


def _free_port() -> str:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return str(listener.getsockname()[1])


def _test_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "ANTHROPIC_API_KEY": "sk-ant-test",
            "DB_IMAGE": "postgres:16-alpine",
            "DB_NAME": "follow_up_test",
            "DB_PASSWORD": "follow_up_test",
            "DB_PORT": _free_port(),
            "DB_TYPE": "postgres",
            "DB_USER": "follow_up_test",
            "EVENTS_FILENAME": "events.jsonl",
            "LITELLM_DB_NAME": "litellm_test",
            "LITELLM_DB_PASSWORD": "litellm_test",
            "LITELLM_DB_USER": "litellm_test",
            "LITELLM_MASTER_KEY": "sk-test-master-key",
            "LITELLM_PROXY_PORT": _free_port(),
            "LITELLM_SALT_KEY": "sk-test-salt-key-0000000000000000",
            "QUOTES_FILENAME": "quotes.json",
            "SEED_DIR": "/seed",
            "STORE_MODEL_IN_DB": "True",
        }
    )
    return environment


@pytest.fixture(scope="session")
def app_stack() -> ComposeStack:
    stack = ComposeStack(
        project=f"follow-up-engine-test-{os.getpid()}",
        environment=_test_environment(),
    )

    try:
        startup = stack.run("up", "-d", "--build", "app")
        assert startup.returncode == 0, startup.stdout

        container_id = stack.run("ps", "-a", "-q", "app")
        assert container_id.returncode == 0, container_id.stdout
        assert container_id.stdout.strip(), stack.run("ps", "-a").stdout
        try:
            wait = subprocess.run(
                ["docker", "wait", container_id.stdout.strip()],
                text=True,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
                timeout=120,
            )
        except subprocess.TimeoutExpired:
            pytest.fail(
                "app did not exit within 120 seconds:\n"
                + stack.run("logs", "app").stdout
            )
        assert wait.returncode == 0, wait.stdout
        assert wait.stdout.strip() == "0", stack.run("logs", "app").stdout
        yield stack
    finally:
        stack.run("down", "--volumes", "--remove-orphans")


@pytest.fixture
def postgres_connection(app_stack: ComposeStack):
    environment = app_stack.environment
    engine = create_engine(
        "postgresql+psycopg2://{user}:{password}@127.0.0.1:{port}/{name}".format(
            user=environment["DB_USER"],
            password=environment["DB_PASSWORD"],
            port=environment["DB_PORT"],
            name=environment["DB_NAME"],
        )
    )
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()
    engine.dispose()
