from __future__ import annotations

from dory_core.status import DoryStatus


def render_metrics(status: DoryStatus) -> str:
    return "\n".join(
        [
            "# HELP dory_corpus_files Number of markdown files in the corpus.",
            "# TYPE dory_corpus_files gauge",
            f"dory_corpus_files {status.corpus_files}",
            "# HELP dory_indexed_files Number of files indexed in sqlite.",
            "# TYPE dory_indexed_files gauge",
            f"dory_indexed_files {status.files_indexed}",
            "# HELP dory_indexed_chunks Number of chunks indexed in sqlite.",
            "# TYPE dory_indexed_chunks gauge",
            f"dory_indexed_chunks {status.chunks_indexed}",
            "# HELP dory_indexed_vectors Number of vectors indexed in the vector store.",
            "# TYPE dory_indexed_vectors gauge",
            f"dory_indexed_vectors {status.vectors_indexed}",
            "",
        ]
    )
