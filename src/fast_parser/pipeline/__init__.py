"""Pluggable enrichment.

Stage 1 registers stubs that leave `category`, `sentiment` and `fake_score`
empty and keep `pipeline_status=pending`. A later stage replaces a stub in
`PROCESSOR_REGISTRY` (or inserts its own processor ahead of `build_pipeline`)
and turns `pipeline.enabled` on. Processors see a plain `ArticleView` and
return a partial `ProcessorResult`, so they do not depend on SQLAlchemy.
"""

from fast_parser.pipeline.base import ArticleProcessor, ArticleView, ProcessorResult
from fast_parser.pipeline.runner import Pipeline
from fast_parser.pipeline.stubs import build_processors

__all__ = [
    "ArticleProcessor",
    "ArticleView",
    "Pipeline",
    "ProcessorResult",
    "build_processors",
]
