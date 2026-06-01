from pydantic import BaseModel, Field
from typing import Any, Optional
from datetime import datetime


class BatchScoreRequest(BaseModel):
    bairros: list[str]


class NarrativeRequest(BaseModel):
    cityName: str
    riskData: dict
    consensusData: Optional[dict] = None
    nearbyReports: Optional[list] = None
    apacBoletim: Optional[Any] = None
    weather: Optional[dict] = None


class CreateReportPayload(BaseModel):
    tipo: str = Field(..., pattern="^(alagamento|deslizamento|queda_arvore|via_intransitavel|poste_caido|buraco|lixo|iluminacao|outro)$")
    severidade: Optional[str] = Field(None, pattern="^(leve|moderado|grave)$")
    lat: float = Field(..., ge=-8.16, le=-7.93)
    lon: float = Field(..., ge=-35.02, le=-34.83)
    user_lat: float = Field(..., ge=-8.16, le=-7.93)
    user_lon: float = Field(..., ge=-35.02, le=-34.83)
    descricao: Optional[str] = Field(None, max_length=280)
    bairro: Optional[str] = None


class LikePayload(BaseModel):
    vote: int = Field(..., ge=-1, le=1)


class ReportOut(BaseModel):
    id: str
    tipo: str
    severidade: str
    lat: float
    lon: float
    bairro: Optional[str]
    descricao: Optional[str]
    confirmacoes: int
    created_at: datetime


class RouteRiskRequest(BaseModel):
    origem_lat: float
    origem_lon: float
    destino_lat: float
    destino_lon: float
    perfil: str = "driving-car"
    rain_next: float = 0.0
    origem_nome: Optional[str] = None
    destino_nome: Optional[str] = None
