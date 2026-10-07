"""
API Resquest and Response Models
Pydantic models for input validation and responde structure

"""
from typing import Literal
from pydantic import BaseModel, Field, ConfigDict, model_validator
from datetime import datetime, timezone
from app.contracts import Citation, SelectionFilters


class ChatRequest(BaseModel):
    """Incoming chat request."""

    model_config = ConfigDict(extra="forbid")
    filters: SelectionFilters = Field(default_factory=SelectionFilters)
    message: str = Field(
        ...,
        min_length=1, # First line of defense
        max_length=10000, # Second line of defense
        description="The use's message to the agent"
    )

    thread_id: str = Field(
        default="default",
        min_length=1, max_length=256,
        description="Conversation thread ID" # ID attached to each conversation
    )

class ChatResponse(BaseModel):
    """Chat responde return to the client"""
    response: str
    thread_id: str
    model_config = ConfigDict(extra="forbid")
    model_used: str | None = None
    cached: bool= False
    processing_time_ms: float
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    request_id: str = Field(min_length=1)
    status: Literal["answered", "abstained", "needs_clarification"]
    corpus_generation_id: str = Field(min_length=1)
    citations: list[Citation] = Field(default_factory=list)
    reason_code: Literal["insufficient_evidence", "invalid_candidate", "unresolved_relation", "ambiguous_instrument", "ambiguous_time", "budget_exceeded"] | None = None

    @model_validator(mode="after")
    def proof_status(self):
        if self.status == "answered" and (not self.citations or self.reason_code is not None):
            raise ValueError("Resposta exige citações e ausência de motivo de abstenção")
        if self.status != "answered" and (self.citations or self.reason_code is None):
            raise ValueError("Resposta controlada exige motivo e nenhuma citação")
        return self
    
class HeathResponse(BaseModel):
    """Health check response"""
    status: str
    environment: str
    version: str = "1.0.0"
    checks: dict = {}

class MetricsResponse(BaseModel):
    """Metrics endpoint response."""
    total_requests: int
    total_erros: int
    error_rate: str
    avg_latency_ms: float
    cache_hit_rate: str
    total_input_tokens: int 
    total_output_tokens: int
    token_efficiency: float
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())


class ErrorResponse(BaseModel):
    """Standardized error response."""
    model_config = ConfigDict(extra="forbid")
    code: Literal["unauthorized", "forbidden", "invalid_request", "rate_limited", "service_unavailable", "request_timeout", "internal_error"]
    message: str
    request_id: str = Field(min_length=1)
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
