from services.ai_validator import validate_report


def test_alagamento_com_chuva_apac_aumenta_score():
    result = validate_report({
        "type": "alagamento",
        "description": "Rua com muita água acumulada",
        "photo_ai_description": "água cobrindo parte da via",
        "photo_ai_confidence": 0.8,
        "weather": {"rain_1h_mm": 12, "rain_24h_mm": 35},
    })
    assert result["score"] >= 0.75
    assert result["flags"] == []


def test_alagamento_sem_chuva_e_sem_agua_reduz_score():
    result = validate_report({
        "type": "alagamento",
        "description": "ocorrência na rua",
        "photo_ai_description": "calçada seca",
        "weather": {"rain_1h_mm": 0, "rain_24h_mm": 0},
    })
    assert result["score"] < 0.5
    assert "alagamento_sem_chuva_ou_foto" in result["flags"]
