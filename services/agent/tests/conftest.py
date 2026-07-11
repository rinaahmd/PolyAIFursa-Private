import pytest

import app as agent_app


@pytest.fixture(autouse=True)
def _reset_agent_module_state():
    """app.py keeps a few module-level dicts (_current_working_image,
    _processed_images, _detections_by_chat, _object_reference_hints_by_chat)
    that are intentionally NOT request-scoped, so edits can chain across
    turns within the same chat_id. Without resetting them between tests,
    one test's leftover state (e.g. a blurred image cached under the
    default "chat" key) leaks into the next test that reuses that key."""
    agent_app._current_working_image.clear()
    agent_app._processed_images.clear()
    agent_app._detections_by_chat.clear()
    agent_app._object_reference_hints_by_chat.clear()
    yield
    agent_app._current_working_image.clear()
    agent_app._processed_images.clear()
    agent_app._detections_by_chat.clear()
    agent_app._object_reference_hints_by_chat.clear()
