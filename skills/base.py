# -*- coding: utf-8 -*-
"""Shared skill protocol types."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable


@dataclass(frozen=True)
class SkillSpec:
    name: str
    description: str
    arguments: dict


SkillFunction = Callable[..., Any]

