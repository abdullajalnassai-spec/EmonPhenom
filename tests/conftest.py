import pytest

from emonphenom.db import fresh


@pytest.fixture
def conn():
    connection = fresh(":memory:")
    yield connection
    connection.close()
