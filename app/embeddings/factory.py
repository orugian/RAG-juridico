"""Explicit settings binding. No download, provider call or hash fallback."""
from app.config import Settings
from app.embeddings.artifacts import MODEL_ID, REVISION
from app.embeddings.qwen import QwenEmbeddings, QwenProfile


def build_local_embeddings(settings: Settings) -> QwenEmbeddings:
    if (settings.embedding_backend != 'local' or settings.embedding_model != MODEL_ID
            or settings.embedding_revision != REVISION or settings.embedding_dimension != 1024
            or settings.embedding_artifact_dir is None):
        raise ValueError('Local embedding selection must match the pinned Qwen artifact')
    profile = QwenProfile(max_input_tokens=settings.embedding_max_input_tokens,
                          max_batch_size=settings.embedding_max_batch_size,
                          max_batch_tokens=settings.embedding_max_batch_tokens,
                          cpu_threads=settings.embedding_cpu_threads)
    return QwenEmbeddings(settings.embedding_artifact_dir, profile)
