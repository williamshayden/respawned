import pytest

from follow_up_engine.core.context import BusinessContext


def test_business_context_rejects_invalid_iana_timezone():
    with pytest.raises(ValueError) as exc_info:
        BusinessContext("Not/A-Timezone")

    message = str(exc_info.value)
    assert "IANA" in message
    assert "Not/A-Timezone" in message
