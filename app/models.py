"""
API Resquest and Response Models
Pydantic models for input validation and responde structure

"""
from functools import cache
from concurrent.futures import thread
from pydantic import BaseModel, Field
from datetime import datetime, timezone


class ChatRequest(BaseModel):
    """Incoming chat request."""

    message: str = Field(
        ...,
        min_length=1, # First line of defense
        max_length=10000, # Second line of defense
        description="The use's message to the agent"
    )

    thread_id: str = Field(
        default="default",
        description="Conversation thread ID" # ID attached to each conversation
    )

class ChatResponse(BaseModel):
    """Chat responde return to the client"""
    response: str
    thread_id: str
    model_used: str
    cached: bool= False
    processing_time_ms: float
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
    
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
    error: str
    message: str
    detail: str | None = None
    request_id: str | None = None
    timestamp: str = Field(default_factory=lambda: datetime.now(timezone.utc).isoformat())
