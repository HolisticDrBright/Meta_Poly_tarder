"""Specialist layer — dedicated LLM agents backed by real data sources."""

from backend.strategies.specialists.base import (
    Specialist,
    SpecialistOpinion,
    entropy_edge_passes,
)

__all__ = ["Specialist", "SpecialistOpinion", "entropy_edge_passes"]
