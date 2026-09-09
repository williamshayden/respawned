import os
import socket
import subprocess
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

import pytest
from sqlalchemy import create_engine

from respawned.db.helpers.pg_connect import create_tables


ROOT = Path(__file__).resolve().parents[1]


def pytest_addoption(parser):
    parser.addoption(
        "--postgres-url",
        help="Use an existing PostgreSQL server for database tests in an isolated schema",
    )


@dataclass(frozen=True)
class ComposeStack:
    project: str
    environment: dict[str, str] = field(repr=False)

    def run(
        self, *args: str, timeout: float = 120
    ) -> subprocess.CompletedProcess[str]:
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
            timeout=timeout,
        )


def _free_port() -> str:
    with socket.socket() as listener:
        listener.bind(("127.0.0.1", 0))
        return str(listener.getsockname()[1])


def _test_environment() -> dict[str, str]:
    environment = os.environ.copy()
    environment.update(
        {
            "APP_PORT": _free_port(),
            "BIND_HOST": "127.0.0.1",
            "RESPAWNED_PROCESS_TOKEN": "",
            "DB_IMAGE": "postgres:16-alpine",
            "DB_DATA_MOUNT": "/var/lib/postgresql/data",
            "DB_NAME": "respawned_test",
            "DB_PASSWORD": "respawned_test",
            "DB_PORT": _free_port(),
            "DB_USER": "respawned_test",
            # Compose interpolates inactive profiles too. These placeholders
            # keep config output clean without starting or linking LiteLLM.
            "LITELLM_DB_NAME": "litellm_test",
            "LITELLM_DB_PASSWORD": "litellm_test",
            "LITELLM_DB_USER": "litellm_test",
            "LITELLM_MASTER_KEY": "sk-test-master-key",
            "LITELLM_PROXY_PORT": _free_port(),
            "LITELLM_SALT_KEY": "sk-test-salt-key-0000000000000000",
            "LITELLM_UPSTREAM_API_KEY": "not-used-by-app-profile",
            "LITELLM_UPSTREAM_MODEL": "not-used-by-app-profile",
            "STORE_MODEL_IN_DB": "False",
        }
    )
    return environment


@pytest.fixture(scope="session")
def compose_environment() -> dict[str, str]:
    return _test_environment()


@contextmanager
def running_app_stack(environment: dict[str, str]):
    stack = ComposeStack(
        project=f"respawned-test-{uuid4().hex[:12]}",
        environment=environment,
    )

    try:
        startup = stack.run(
            "up",
            "-d",
            "--build",
            "--wait",
            "--wait-timeout",
            "120",
            "app",
            timeout=300,
        )
        assert startup.returncode == 0, startup.stdout
        yield stack
    finally:
        stack.run(
            "down",
            "--volumes",
            "--remove-orphans",
            "--rmi",
            "local",
            timeout=60,
        )


@pytest.fixture(scope="session")
def app_stack(compose_environment: dict[str, str]) -> ComposeStack:
    with running_app_stack(compose_environment) as stack:
        yield stack


@pytest.fixture
def lifecycle_stack() -> ComposeStack:
    """A separate owned stack whose restart cannot disrupt other DB fixtures."""
    with running_app_stack(_test_environment()) as stack:
        yield stack


@pytest.fixture(scope="session")
def postgres_engine(request):
    url = request.config.getoption("--postgres-url")
    if url is None:
        environment = request.getfixturevalue("app_stack").environment
        url = "postgresql+psycopg2://{user}:{password}@127.0.0.1:{port}/{name}".format(
            user=environment["DB_USER"],
            password=environment["DB_PASSWORD"],
            port=environment["DB_PORT"],
            name=environment["DB_NAME"],
        )
    # Only this generated schema is created or removed; existing data is untouched.
    schema = f"respawned_test_{uuid4().hex}"
    engine = create_engine(url, connect_args={"options": f"-csearch_path={schema}"})
    schema_created = False
    try:
        with engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{schema}"')
        schema_created = True
        create_tables(engine)
        yield engine
    finally:
        try:
            if schema_created:
                with engine.begin() as connection:
                    connection.exec_driver_sql(f'DROP SCHEMA "{schema}" CASCADE')
        finally:
            engine.dispose()


@pytest.fixture
def postgres_connection(postgres_engine):
    engine = postgres_engine
    with engine.connect() as connection:
        transaction = connection.begin()
        try:
            yield connection
        finally:
            transaction.rollback()
