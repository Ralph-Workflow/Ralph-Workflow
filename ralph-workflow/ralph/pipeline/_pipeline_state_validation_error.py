from __future__ import annotations


class PipelineStateValidationError(RuntimeError, TypeError):
    pass


__all__ = ["PipelineStateValidationError"]
