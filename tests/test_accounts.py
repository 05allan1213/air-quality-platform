import json

import pytest
from accounts.views import credentials
from django.test import RequestFactory


@pytest.mark.parametrize("text", ["", "x\x00y", "\ud800", "a" * 1025])
def test_credentials_reject_text_unsuitable_for_auth(text):
    request = RequestFactory().post(
        "/", json.dumps({"username": text, "password": "password"}), content_type="application/json"
    )
    assert credentials(request) is None
