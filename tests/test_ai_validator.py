from services.ai_validator import validate_report


def test_alagamento_com_chuva_apac_aumenta_score():
    """Foto coerente + chuva alta + vision urbana → alta confiança."""
    result = validate_report({
        "type": "alagamento",
        "description": "Rua com muita água acumulada",
        "photo_url": "https://example/alag.jpg",
        "photo_ai_description": "água cobrindo parte da via",
        "photo_ai_confidence": 0.8,
        "photo_ai_is_urban_problem": True,
        "weather": {"rain_1h_mm": 12, "rain_24h_mm": 35},
    })
    assert result["score"] >= 0.75
    assert result["flags"] == []
    assert result["bucket_hint"] in ("revisar", "auto_validado")


def test_alagamento_sem_chuva_e_sem_agua_reduz_score():
    """Texto pobre + sem chuva → flag negativo."""
    result = validate_report({
        "type": "alagamento",
        "description": "ocorrência na rua",
        "photo_ai_description": "calçada seca",
        "weather": {"rain_1h_mm": 0, "rain_24h_mm": 0},
    })
    assert result["score"] < 0.5
    assert "alagamento_sem_chuva_ou_foto" in result["flags"]


# ───── V4: gates novos ──────────────────────────────────────────────

def test_gate_foto_nao_urbana_zera_score():
    """Gengar: foto presente mas is_urban_problem=False → bucket filtrado."""
    result = validate_report({
        "type": "alagamento",
        "description": "",
        "photo_url": "https://example/gengar.jpg",
        "photo_ai_description": "figura roxa de desenho animado",
        "photo_ai_confidence": 0.9,
        "photo_ai_is_urban_problem": False,
        "weather": {"rain_1h_mm": 8},
    })
    assert result["score"] <= 0.15
    assert "nao_urbano" in result["flags"]
    assert result["bucket_hint"] == "filtrado"


def test_gate_confidence_baixa_zera_score():
    """Confidence < 0.4 → bucket filtrado mesmo sem flag não-urbano."""
    result = validate_report({
        "type": "buraco",
        "photo_url": "https://example/blurry.jpg",
        "photo_ai_confidence": 0.30,
        "photo_ai_is_urban_problem": True,
        "weather": {},
    })
    assert result["score"] <= 0.15
    assert "baixa_confianca_foto" in result["flags"]
    assert result["bucket_hint"] == "filtrado"


def test_compatibilidade_v3_sem_is_urban():
    """Reports antigos (sem photo_ai_is_urban_problem) não são bloqueados pelos gates."""
    result = validate_report({
        "type": "alagamento",
        "description": "enchente na avenida",
        "photo_url": "https://example/v3.jpg",
        "photo_ai_description": "agua na rua",
        "photo_ai_confidence": 0.7,
        # sem photo_ai_is_urban_problem (None)
        "weather": {"rain_24h_mm": 20},
    })
    # Não pode ser filtrado por gate (is_urban=None ≠ False)
    assert result["score"] > 0.20
    assert "nao_urbano" not in result["flags"]


def test_outro_com_foto_tem_penalidade():
    """type='outro' com foto: categoria genérica perde pontos."""
    result = validate_report({
        "type": "outro",
        "photo_url": "https://example/x.jpg",
        "photo_ai_description": "coisa qualquer",
        "photo_ai_confidence": 0.7,
        "photo_ai_is_urban_problem": True,
        "weather": {},
    })
    assert "tipo_generico_com_foto" in result["flags"]
