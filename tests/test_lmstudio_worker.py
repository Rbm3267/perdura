"""LMStudioWorker (perdura.py) — generate() takes an injectable `post`, so
this module never touches the network, matching perdura_connectors.py's
offline-test convention: no LM Studio server, no key, no network."""

from perdura import DEFAULT_LMSTUDIO_URL, DEFAULT_QWEN_MODEL, LMStudioWorker


def test_generate_posts_input_and_parses_message_output():
    calls = []

    def fake_post(url, payload, headers):
        calls.append((url, payload, headers))
        return {"output": [{"type": "reasoning", "content": "thinking..."},
                           {"type": "message", "content": '{"new_nodes": []}'}]}

    worker = LMStudioWorker(api_key="tok", post=fake_post)
    result = worker.generate("some prompt")

    assert result == '{"new_nodes": []}'
    url, payload, headers = calls[0]
    assert url == f"{DEFAULT_LMSTUDIO_URL}/api/v1/chat"
    assert payload == {"model": DEFAULT_QWEN_MODEL, "input": "some prompt",
                       "store": False}
    assert headers["Authorization"] == "Bearer tok"


def test_generate_concatenates_multiple_message_items_and_skips_others():
    def fake_post(url, payload, headers):
        return {"output": [
            {"type": "message", "content": "a"},
            {"type": "tool_call", "tool": "x", "output": "y"},
            {"type": "invalid_tool_call", "reason": "bad args"},
            {"type": "message", "content": "b"},
        ]}

    worker = LMStudioWorker(api_key="tok", post=fake_post)
    assert worker.generate("p") == "ab"


def test_generate_omits_authorization_header_without_a_key(monkeypatch):
    monkeypatch.delenv("LM_STUDIO_API_KEY", raising=False)

    def fake_post(url, payload, headers):
        assert "Authorization" not in headers
        return {"output": []}

    worker = LMStudioWorker(post=fake_post)
    assert worker.generate("p") == ""


def test_generate_reads_api_key_from_env_by_default(monkeypatch):
    monkeypatch.setenv("LM_STUDIO_API_KEY", "env-tok")

    def fake_post(url, payload, headers):
        assert headers["Authorization"] == "Bearer env-tok"
        return {"output": []}

    worker = LMStudioWorker(post=fake_post)
    worker.generate("p")


def test_custom_model_and_base_url_are_used_and_trailing_slash_stripped():
    def fake_post(url, payload, headers):
        assert url == "http://192.168.7.80:1234/api/v1/chat"
        assert payload["model"] == "ibm/granite-4-micro"
        return {"output": []}

    worker = LMStudioWorker(model="ibm/granite-4-micro",
                            base_url="http://192.168.7.80:1234/",
                            api_key="tok", post=fake_post)
    worker.generate("p")
