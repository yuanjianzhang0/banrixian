# -*- coding: utf-8 -*-
"""Unified error types and error response formatting for Banrixian backend."""

from __future__ import annotations

from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    """Standard error codes used throughout the backend."""
    # Authentication & Authorization
    AUTH_MISSING = "AUTH_MISSING"
    AUTH_INVALID = "AUTH_INVALID"
    AUTH_EXPIRED = "AUTH_EXPIRED"
    
    # User & Profile
    USER_NOT_FOUND = "USER_NOT_FOUND"
    USER_ALREADY_EXISTS = "USER_ALREADY_EXISTS"
    INVALID_CREDENTIALS = "INVALID_CREDENTIALS"
    
    # Database Errors
    DB_CONNECTION_FAILED = "DB_CONNECTION_FAILED"
    DB_QUERY_FAILED = "DB_QUERY_FAILED"
    DB_CONSTRAINT_VIOLATION = "DB_CONSTRAINT_VIOLATION"
    
    # LLM/AI Errors
    LLM_UNAVAILABLE = "LLM_UNAVAILABLE"
    LLM_API_ERROR = "LLM_API_ERROR"
    LLM_TIMEOUT = "LLM_TIMEOUT"
    LLM_INVALID_RESPONSE = "LLM_INVALID_RESPONSE"
    
    # Skill/Tool Errors
    SKILL_NOT_FOUND = "SKILL_NOT_FOUND"
    SKILL_EXECUTION_FAILED = "SKILL_EXECUTION_FAILED"
    WEATHER_UNAVAILABLE = "WEATHER_UNAVAILABLE"
    
    # Input Validation
    INVALID_INPUT = "INVALID_INPUT"
    MISSING_PARAMETER = "MISSING_PARAMETER"
    
    # Generic/Unknown
    INTERNAL_ERROR = "INTERNAL_ERROR"
    SERVICE_UNAVAILABLE = "SERVICE_UNAVAILABLE"


class BanrixianError(Exception):
    """Base exception for all Banrixian-specific errors."""
    
    def __init__(
        self,
        message: str,
        code: str = ErrorCode.INTERNAL_ERROR,
        status_code: int = 500,
        details: dict[str, Any] | None = None,
    ):
        self.message = message
        self.code = code
        self.status_code = status_code
        self.details = details or {}
        super().__init__(self.message)
    
    def to_dict(self) -> dict[str, Any]:
        """Convert error to JSON-serializable dict."""
        return {
            "error": {
                "code": self.code,
                "message": self.message,
                "details": self.details,
            }
        }
    
    def to_sse_event(self) -> dict[str, Any]:
        """Convert error to SSE error event format."""
        return {
            "type": "error",
            "code": self.code,
            "content": self.message,
            "details": self.details,
        }


class AuthenticationError(BanrixianError):
    """Raised when authentication fails."""
    
    def __init__(self, message: str = "Authentication required", **kwargs):
        super().__init__(message, code=ErrorCode.AUTH_MISSING, status_code=401, **kwargs)


class AuthorizationError(BanrixianError):
    """Raised when user lacks permission."""
    
    def __init__(self, message: str = "Insufficient permissions", **kwargs):
        super().__init__(message, code=ErrorCode.AUTH_INVALID, status_code=403, **kwargs)


class UserNotFoundError(BanrixianError):
    """Raised when user lookup fails."""
    
    def __init__(self, message: str = "User not found", **kwargs):
        super().__init__(message, code=ErrorCode.USER_NOT_FOUND, status_code=404, **kwargs)


class DatabaseError(BanrixianError):
    """Raised when database operation fails."""
    
    def __init__(self, message: str, code: str = ErrorCode.DB_QUERY_FAILED, **kwargs):
        super().__init__(message, code=code, status_code=500, **kwargs)


class LLMError(BanrixianError):
    """Base exception for LLM-related errors."""
    
    def __init__(self, message: str, code: str = ErrorCode.LLM_API_ERROR, **kwargs):
        super().__init__(message, code=code, status_code=503, **kwargs)


class LLMUnavailableError(LLMError):
    """Raised when no LLM provider is available."""
    
    def __init__(self, message: str = "No LLM provider is configured", **kwargs):
        super().__init__(message, code=ErrorCode.LLM_UNAVAILABLE, **kwargs)


class LLMTimeoutError(LLMError):
    """Raised when LLM request times out."""
    
    def __init__(self, message: str = "LLM request timeout", **kwargs):
        super().__init__(message, code=ErrorCode.LLM_TIMEOUT, **kwargs)


class WeatherError(BanrixianError):
    """Raised when weather skill fails."""
    
    def __init__(self, message: str, **kwargs):
        super().__init__(message, code=ErrorCode.WEATHER_UNAVAILABLE, status_code=503, **kwargs)


class SkillError(BanrixianError):
    """Raised when skill execution fails."""
    
    def __init__(self, message: str, **kwargs):
        super().__init__(message, code=ErrorCode.SKILL_EXECUTION_FAILED, status_code=500, **kwargs)


class ValidationError(BanrixianError):
    """Raised when input validation fails."""
    
    def __init__(self, message: str, **kwargs):
        super().__init__(message, code=ErrorCode.INVALID_INPUT, status_code=400, **kwargs)
