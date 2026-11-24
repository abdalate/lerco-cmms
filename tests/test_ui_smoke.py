"""Every role opens its pages without an exception."""
import os

import pytest
from streamlit.testing.v1 import AppTest

APP = os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "app.py")

PAGES = {"PLN": ["home", "review", "sr", "track", "groups", "pm", "tir", "daily", "monthly", "dash", "master"],
         "REF": ["my", "pm", "monthly"], "OM": ["my", "pm", "monthly"],
         "M": ["home", "track", "my", "pm", "daily", "monthly"],
         "E": ["home", "monthly"], "I": ["home", "monthly"],
         "INSP": ["tir", "monthly"]}


@pytest.mark.parametrize("role,page", [(r, p) for r, ps in PAGES.items() for p in ps])
def test_page_opens(role, page):
    at = AppTest.from_file(APP, default_timeout=90)
    at.session_state["role"] = role
    at.session_state[f"menu_{role}"] = page
    at.run()
    assert not at.exception, [e.value for e in at.exception]
