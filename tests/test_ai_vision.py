from services.ai_vision import _parse_jsonish


def test_parse_jsonish_accepts_markdown_wrapped_json():
    raw = """```json
    {"description":"buraco grande na via","type":"buraco","confidence":0.82}
    ```"""
    result = _parse_jsonish(raw)
    assert result["suggested_type"] == "buraco"
    assert result["confidence"] == 0.82


def test_parse_jsonish_falls_back_to_text_keywords():
    result = _parse_jsonish("há água acumulada na rua")
    assert result["suggested_type"] == "alagamento"
    assert result["confidence"] > 0
