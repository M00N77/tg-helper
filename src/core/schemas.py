from pydantic import BaseModel, Field


class SentimentRiskResult(BaseModel):
    sentiment: str = Field(description="positive|negative|neutral|speech")
    has_risk: bool = Field(description="обнаружен ли риск/подвисшая задача/проблема")
    risk_reason: str = Field(default="", description="причина риска если has_risk=true")
