"""
pdf_vectorizer_EC2.py
=================
Module for the PDF vectorization pipeline stored in S3.
Designed to be imported into a RAG system.

Main flow:
    PDF in S3 -> Chunking -> Text extraction (fitz) -> Embedding (mxbai-embed-large) -> S3 Vectors

Dependencies:
    boto3, pymupdf (fitz), Pillow (PIL), requests

Basic usage:
    from rag_agent.pdf_vectorizer_EC2 import batch_process_pdfs, process_pdf_to_vectors
"""

from __future__ import annotations

import copy
import io
import logging
import re
import time
from datetime import datetime, timezone
from typing import Any

import boto3
import fitz  # PyMuPDF
import requests
from PIL import Image

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Default constants (can be overridden via function parameters)
# ---------------------------------------------------------------------------

DEFAULT_REGION = "us-east-2"
DEFAULT_EMBEDDINGS_ENDPOINT = "http://ec2-3-208-23-94.compute-1.amazonaws.com:11434"
DEFAULT_EMBEDDINGS_MODEL = "mxbai-embed-large"
DEFAULT_EMBEDDING_DIMS = 1024
DEFAULT_EMBEDDINGS_TIMEOUT = 30  # seconds per embedding request
DEFAULT_MAX_PAGES_PER_CHUNK = 2
DEFAULT_MAX_EMBED_CHARS = 6_000
DEFAULT_COMPRESSION_TARGET_MB = 4.0
DEFAULT_COMPRESSION_QUALITY_STEPS = [85, 70, 50, 30]
DEFAULT_CHUNK_SIZE_LIMIT_MB = 4.5
DEFAULT_VECTOR_BATCH_SIZE = 100
DEFAULT_CHUNK_STRATEGY = "length"  # Supported values: "length", "section"
DEFAULT_CHUNK_OVERLAP = 100  # Overlap in characters between length-based chunks
SECTION_HEADING_PATTERN = (
    r"(?m)^#{1,6}\s+.+$|^[A-Z][^\n]{0,80}\n[-=]{3,}"  # Markdown + underline headings
)


# ---------------------------------------------------------------------------
# Filename utilities
# ---------------------------------------------------------------------------


def sanitize_filename(filename: str) -> str:
    """Clean a filename for use as a vector key.

    Keeps only alphanumeric characters, spaces, hyphens, parentheses, and
    square brackets. Strips the file extension and normalizes consecutive spaces.

    Args:
        filename: Original filename (may include extension).

    Returns:
        Sanitized lowercase name without extension.
    """
    name = filename.rsplit(".", 1)[0] if "." in filename else filename
    name = name.lower()
    name = re.sub(r"[^a-zA-Z0-9\s\-\(\)\[\]]", "-", name)
    name = re.sub(r"\s+", " ", name).strip()
    return name


# ---------------------------------------------------------------------------
# S3 operations
# ---------------------------------------------------------------------------


def list_s3_folders(
    bucket_name: str,
    prefix: str = "",
    s3_client: Any | None = None,
) -> list[str]:
    """List all 'folders' (common prefixes) within an S3 bucket.

    Automatically paginates when the bucket has many entries.

    Args:
        bucket_name: Name of the S3 bucket.
        prefix: Prefix to start the search from (e.g. 'uploads/').
        s3_client: Reusable boto3 S3 client. A new one is created if None.

    Returns:
        List of strings with the prefixes of the folders found.
    """
    s3 = s3_client or boto3.client("s3")
    folders: list[str] = []
    continuation_token: str | None = None

    while True:
        params: dict[str, Any] = {
            "Bucket": bucket_name,
            "Prefix": prefix,
            "Delimiter": "/",
        }
        if continuation_token:
            params["ContinuationToken"] = continuation_token

        response = s3.list_objects_v2(**params)
        folders.extend(cp["Prefix"] for cp in response.get("CommonPrefixes", []))

        if not response.get("IsTruncated"):
            break
        continuation_token = response.get("NextContinuationToken")

    logger.debug("Found %d folders under '%s'", len(folders), prefix)
    return folders


def list_s3_files(
    bucket_name: str,
    folder_path: str,
    extension_filter: str | None = None,
    s3_client: Any | None = None,
) -> list[str]:
    """List files directly inside an S3 folder (non-recursive).

    Args:
        bucket_name: Name of the S3 bucket.
        folder_path: Folder path (must end with '/').
        extension_filter: If provided (e.g. '.pdf'), returns only files with
            that extension (case-insensitive). None returns all files.
        s3_client: Reusable boto3 S3 client.

    Returns:
        List of S3 keys for the files found.
    """
    s3 = s3_client or boto3.client("s3")
    response = s3.list_objects_v2(
        Bucket=bucket_name,
        Prefix=folder_path,
        Delimiter="/",
    )

    files: list[str] = []
    for obj in response.get("Contents", []):
        key = obj["Key"]
        if key == folder_path:
            continue
        relative = key[len(folder_path) :]
        if "/" in relative:
            continue  # Skip files in nested sub-folders
        if extension_filter and not key.lower().endswith(extension_filter.lower()):
            continue
        files.append(key)

    return files


# ---------------------------------------------------------------------------
# PDF operations
# ---------------------------------------------------------------------------


def compress_pdf_chunk(
    chunk_pdf: fitz.Document,
    target_size_mb: float = DEFAULT_COMPRESSION_TARGET_MB,
    quality_steps: list[int] = DEFAULT_COMPRESSION_QUALITY_STEPS,
) -> bytes | None:
    """Compress a PDF chunk by reducing the JPEG quality of its images.

    Iterates over the provided quality levels until the resulting PDF falls
    below `target_size_mb`. Pages without images are copied directly.

    Args:
        chunk_pdf: PyMuPDF document to compress.
        target_size_mb: Target size in MB.
        quality_steps: JPEG quality levels to try in descending order (0-100).

    Returns:
        Compressed PDF bytes, or None if no quality level met the target.
    """
    for quality in quality_steps:
        try:
            compressed_pdf = fitz.open()

            for page_num in range(len(chunk_pdf)):
                page = chunk_pdf[page_num]
                new_page = compressed_pdf.new_page(
                    width=page.rect.width, height=page.rect.height
                )

                if page.get_images(full=True):
                    # Rasterize the page and re-encode as JPEG
                    pix = page.get_pixmap(matrix=fitz.Matrix(2, 2))
                    img = Image.open(io.BytesIO(pix.tobytes("png")))
                    if img.mode == "RGBA":
                        img = img.convert("RGB")

                    buf = io.BytesIO()
                    img.save(buf, format="JPEG", quality=quality, optimize=True)
                    buf.seek(0)
                    new_page.insert_image(new_page.rect, stream=buf.read())
                else:
                    # No images on this page — copy content directly
                    new_page.show_pdf_page(new_page.rect, chunk_pdf, page_num)

            compressed_bytes = compressed_pdf.write()
            compressed_pdf.close()

            size_mb = len(compressed_bytes) / (1024 * 1024)
            if size_mb <= target_size_mb:
                logger.info(
                    "Chunk compressed to %.2f MB (quality=%d)", size_mb, quality
                )
                return compressed_bytes

            logger.debug("Quality %d -> %.2f MB (still too large)", quality, size_mb)

        except Exception:
            logger.warning("Compression failed at quality=%d", quality, exc_info=True)
            continue

    logger.warning("Could not compress below %.1f MB", target_size_mb)
    return None


def split_pdf_into_chunks(
    pdf_bytes: bytes,
    max_pages_per_chunk: int = DEFAULT_MAX_PAGES_PER_CHUNK,
    chunk_size_limit_mb: float = DEFAULT_CHUNK_SIZE_LIMIT_MB,
    compression_target_mb: float = DEFAULT_COMPRESSION_TARGET_MB,
    compression_quality_steps: list[int] = DEFAULT_COMPRESSION_QUALITY_STEPS,
) -> list[tuple[int, int, bytes]]:
    """Split a PDF into byte chunks ready for processing.

    Each chunk covers a page range. If a chunk exceeds the size limit,
    compression is attempted before including it in the result.

    Args:
        pdf_bytes: Binary content of the full PDF.
        max_pages_per_chunk: Maximum number of pages per chunk.
        chunk_size_limit_mb: Maximum allowed chunk size in MB.
        compression_target_mb: Compression target when a chunk is too large.
        compression_quality_steps: JPEG quality levels used during compression.

    Returns:
        List of tuples (start_page, end_page, chunk_bytes). Chunks that exceed
        the size limit and could not be compressed are skipped.
    """
    pdf_doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    total_pages = len(pdf_doc)
    num_chunks = (total_pages + max_pages_per_chunk - 1) // max_pages_per_chunk

    logger.info(
        "PDF: %d pages -> %d chunks (%d pages/chunk)",
        total_pages,
        num_chunks,
        max_pages_per_chunk,
    )

    result: list[tuple[int, int, bytes]] = []

    for idx in range(num_chunks):
        start = idx * max_pages_per_chunk
        end = min(start + max_pages_per_chunk, total_pages)

        # Build chunk document from the page range
        chunk_doc = fitz.open()
        for p in range(start, end):
            chunk_doc.insert_pdf(pdf_doc, from_page=p, to_page=p)

        chunk_bytes = chunk_doc.write()
        size_mb = len(chunk_bytes) / (1024 * 1024)

        if size_mb > chunk_size_limit_mb:
            logger.warning(
                "Chunk %d/%d is %.2f MB -> attempting compression...",
                idx + 1,
                num_chunks,
                size_mb,
            )
            compressed = compress_pdf_chunk(
                chunk_doc,
                target_size_mb=compression_target_mb,
                quality_steps=compression_quality_steps,
            )
            if compressed is None:
                logger.error(
                    "Chunk %d/%d skipped (could not compress)", idx + 1, num_chunks
                )
                chunk_doc.close()
                continue
            chunk_bytes = compressed

        chunk_doc.close()
        result.append((start, end, chunk_bytes))

    pdf_doc.close()
    return result


# ---------------------------------------------------------------------------
# Text extraction with fitz (PyMuPDF)
# ---------------------------------------------------------------------------


def extract_text_with_fitz(
    chunk_bytes: bytes,
    preserve_layout: bool = False,
) -> str:
    """Extract plain text from a PDF chunk using PyMuPDF (fitz).

    Iterates over every page in the chunk and concatenates the extracted text.
    No external API calls are made — extraction runs entirely locally.

    Args:
        chunk_bytes: Raw bytes of the PDF chunk to process.
        preserve_layout: If True, uses PyMuPDF's layout-preserving extraction
            mode ("blocks"), which attempts to maintain column order and spacing.
            If False (default), uses plain text extraction ("text"), which is
            faster and produces cleaner output for most documents.

    Returns:
        Concatenated text from all pages in the chunk, with pages separated
        by a newline. Returns an empty string if no text could be extracted.
    """
    try:
        pdf_doc = fitz.open(stream=chunk_bytes, filetype="pdf")
        pages_text: list[str] = []

        for page in pdf_doc:
            if preserve_layout:
                # Extract text blocks and join them preserving reading order
                blocks = page.get_text("blocks")
                page_text = "\n".join(
                    block[4]  # block[4] is the text content
                    for block in sorted(
                        blocks, key=lambda b: (b[1], b[0])
                    )  # sort top-to-bottom, left-to-right
                    if block[6] == 0  # block type 0 = text (not image)
                )
            else:
                page_text = page.get_text("text")

            if page_text.strip():
                pages_text.append(page_text.strip())

        pdf_doc.close()
        return "\n".join(pages_text)

    except Exception as exc:
        raise RuntimeError(f"fitz extraction failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Text chunking strategies
# ---------------------------------------------------------------------------


def chunk_text_by_length(
    text: str,
    max_chars: int = DEFAULT_MAX_EMBED_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
) -> list[str]:
    """Split text into fixed-size chunks with optional overlap.

    Slides a window of `max_chars` characters over the text, stepping forward
    by `max_chars - overlap` on each iteration. This guarantees that context
    near chunk boundaries is represented in both adjacent chunks, reducing
    retrieval gaps.

    Args:
        text: Full text to split.
        max_chars: Maximum number of characters per chunk.
        overlap: Number of characters to repeat at the start of the next chunk.
            Must be smaller than max_chars.

    Returns:
        List of text chunks. Returns a list with the original text if it fits
        within max_chars without splitting.

    Raises:
        ValueError: If overlap is greater than or equal to max_chars.
    """
    if overlap >= max_chars:
        raise ValueError(
            f"overlap ({overlap}) must be smaller than max_chars ({max_chars})."
        )

    if len(text) <= max_chars:
        return [text]

    step = max_chars - overlap
    chunks = [text[i : i + max_chars] for i in range(0, len(text), step)]

    # Drop a trailing chunk that contains only overlap and no new content
    if len(chunks) > 1 and len(chunks[-1]) <= overlap:
        chunks = chunks[:-1]

    return chunks


def chunk_text_by_section(
    text: str,
    max_chars: int = DEFAULT_MAX_EMBED_CHARS,
    heading_pattern: str = SECTION_HEADING_PATTERN,
) -> list[str]:
    """Split text into chunks by document sections detected via headings.

    Scans for heading patterns (Markdown `#` headings or underline-style
    headings). Each heading starts a new section. Sections that exceed
    `max_chars` are further split by length to stay within the embedding
    model's input limit.

    Args:
        text: Full text to split.
        max_chars: Maximum characters per chunk. Sections larger than this
            are further split using chunk_text_by_length.
        heading_pattern: Regex pattern used to detect section boundaries.
            Defaults to Markdown headings and underline-style headings.

    Returns:
        List of text chunks, one per section (or sub-chunk if a section
        exceeds max_chars). Returns a length-split result if no headings
        are found.
    """
    matches = list(re.finditer(heading_pattern, text))

    # No headings detected — treat the entire text as one section
    if not matches:
        logger.debug(
            "No section headings found; falling back to length-based chunking."
        )
        return chunk_text_by_length(text, max_chars=max_chars, overlap=0)

    # Build section boundaries from heading positions
    boundaries = [m.start() for m in matches] + [len(text)]
    sections = [text[boundaries[i] : boundaries[i + 1]] for i in range(len(matches))]

    # Prepend any content that appears before the first heading
    preamble = text[: boundaries[0]].strip()
    if preamble:
        sections.insert(0, preamble)

    # Split any section that exceeds max_chars
    chunks: list[str] = []
    for section in sections:
        section = section.strip()
        if not section:
            continue
        if len(section) > max_chars:
            chunks.extend(chunk_text_by_length(section, max_chars=max_chars, overlap=0))
        else:
            chunks.append(section)

    return chunks


def split_text_into_embed_chunks(
    text: str,
    strategy: str = DEFAULT_CHUNK_STRATEGY,
    max_chars: int = DEFAULT_MAX_EMBED_CHARS,
    overlap: int = DEFAULT_CHUNK_OVERLAP,
    heading_pattern: str = SECTION_HEADING_PATTERN,
) -> list[str]:
    """Dispatch text chunking to the selected strategy.

    This is the single entry point used by the pipeline. It delegates to
    chunk_text_by_length or chunk_text_by_section depending on `strategy`.

    Args:
        text: Full extracted text to chunk.
        strategy: Chunking strategy. Accepted values:
            "length" — fixed-size sliding window with overlap.
            "section" — split on document headings; fallback to length if none found.
        max_chars: Maximum characters per chunk.
        overlap: Overlap between consecutive chunks (length strategy only).
        heading_pattern: Regex for heading detection (section strategy only).

    Returns:
        List of text chunks ready for embedding.

    Raises:
        ValueError: If an unsupported strategy value is provided.
    """
    supported = ("length", "section")
    if strategy not in supported:
        raise ValueError(
            f"Unsupported chunk_strategy '{strategy}'. Choose one of: {supported}"
        )

    if strategy == "length":
        return chunk_text_by_length(text, max_chars=max_chars, overlap=overlap)
    else:  # section
        return chunk_text_by_section(
            text, max_chars=max_chars, heading_pattern=heading_pattern
        )


# ---------------------------------------------------------------------------
# Embedding via mxbai-embed-large (Ollama endpoint)
# ---------------------------------------------------------------------------


def embed_text_with_mxbai(
    text: str,
    endpoint: str = DEFAULT_EMBEDDINGS_ENDPOINT,
    model: str = DEFAULT_EMBEDDINGS_MODEL,
    timeout: int = DEFAULT_EMBEDDINGS_TIMEOUT,
) -> list[float]:
    """Create a vector embedding using mxbai-embed-large via the Ollama API.

    Calls the /api/embeddings endpoint on the self-hosted Ollama server.
    Text must already fit within the model's context window — use
    split_text_into_embed_chunks to prepare the text before calling this.

    Args:
        text: Text to vectorize.
        endpoint: Base URL of the Ollama server (e.g. 'http://host:11434').
        model: Ollama model name to use for embeddings.
        timeout: Request timeout in seconds.

    Returns:
        List of floats representing the embedding vector.

    Raises:
        RuntimeError: If the HTTP request fails or the response is malformed.
    """
    try:
        response = requests.post(
            f"{endpoint}/api/embeddings",
            json={"model": model, "prompt": text},
            timeout=timeout,
        )
        response.raise_for_status()
        embedding = response.json().get("embedding", [])

        if not embedding:
            raise RuntimeError("Ollama returned an empty embedding.")

        return embedding

    except requests.RequestException as exc:
        raise RuntimeError(f"mxbai embedding request failed: {exc}") from exc
    except Exception as exc:
        raise RuntimeError(f"mxbai embedding failed: {exc}") from exc


# ---------------------------------------------------------------------------
# Main pipeline: single PDF
# ---------------------------------------------------------------------------


def process_pdf_to_vectors(
    bucket_name: str,
    file_key: str,
    vector_bucket_name: str,
    index_name: str,
    s3_client: Any | None = None,
    s3vectors_client: Any | None = None,
    region: str = DEFAULT_REGION,
    max_pages_per_chunk: int = DEFAULT_MAX_PAGES_PER_CHUNK,
    chunk_size_limit_mb: float = DEFAULT_CHUNK_SIZE_LIMIT_MB,
    compression_target_mb: float = DEFAULT_COMPRESSION_TARGET_MB,
    compression_quality_steps: list[int] = DEFAULT_COMPRESSION_QUALITY_STEPS,
    preserve_layout: bool = False,
    chunk_strategy: str = DEFAULT_CHUNK_STRATEGY,
    max_embed_chars: int = DEFAULT_MAX_EMBED_CHARS,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    section_heading_pattern: str = SECTION_HEADING_PATTERN,
    embeddings_endpoint: str = DEFAULT_EMBEDDINGS_ENDPOINT,
    embeddings_model: str = DEFAULT_EMBEDDINGS_MODEL,
    embeddings_timeout: int = DEFAULT_EMBEDDINGS_TIMEOUT,
    store_vectors: bool = True,
    vector_batch_size: int = DEFAULT_VECTOR_BATCH_SIZE,
) -> list[dict[str, Any]]:
    """Full pipeline: PDF in S3 -> chunks -> text -> embedding -> S3 Vectors.

    Each PDF page-chunk goes through three stages:
      1. Text extraction with fitz (PyMuPDF) — local, no API calls.
      2. Text splitting into embed-ready sub-chunks (controlled by `chunk_strategy`).
      3. Embedding each sub-chunk with mxbai-embed-large via the Ollama endpoint.

    Args:
        bucket_name: Source S3 bucket containing the PDF.
        file_key: S3 key of the PDF file.
        vector_bucket_name: S3 Vectors bucket for storing embeddings.
        index_name: Index name in S3 Vectors.
        s3_client: Reusable boto3 S3 client (created if not provided).
        s3vectors_client: Reusable boto3 s3vectors client (created if not provided).
        region: AWS region for S3 and S3 Vectors clients.
        max_pages_per_chunk: Maximum pages per PDF page-chunk.
        chunk_size_limit_mb: PDF chunk size limit before attempting compression.
        compression_target_mb: Target size when compressing a PDF chunk.
        compression_quality_steps: JPEG quality levels to try during compression.
        preserve_layout: If True, fitz extracts text preserving column/block order.
        chunk_strategy: How to split extracted text before embedding.
            "length" (default) — fixed-size sliding window with overlap.
            "section" — split on document headings.
        max_embed_chars: Maximum characters per embed sub-chunk.
        chunk_overlap: Character overlap between consecutive length-based sub-chunks.
        section_heading_pattern: Regex for detecting section headings.
        embeddings_endpoint: Base URL of the Ollama embeddings server.
        embeddings_model: Ollama model name for embeddings.
        embeddings_timeout: Request timeout in seconds for embedding calls.
        store_vectors: If False, prepares vectors without storing them.
        vector_batch_size: Batch size for put_vectors calls.

    Returns:
        List of dicts with the prepared/stored vector data.
        Returns an empty list if the processing fails entirely.

    Raises:
        ValueError: If an unsupported chunk_strategy value is provided.
    """
    s3 = s3_client or boto3.client("s3")
    s3v = s3vectors_client or boto3.client("s3vectors", region_name=region)

    logger.info("=== Starting: %s (strategy=%s) ===", file_key, chunk_strategy)

    try:
        # Step 1: Download PDF from S3
        logger.info("[1/3] Downloading PDF from s3://%s/%s", bucket_name, file_key)
        pdf_object = s3.get_object(Bucket=bucket_name, Key=file_key)
        pdf_bytes = pdf_object["Body"].read()
        logger.info("      Size: %.2f MB", len(pdf_bytes) / (1024 * 1024))

        # Step 2: Split into page-chunks
        logger.info(
            "[2/3] Splitting into chunks (max %d pages)...", max_pages_per_chunk
        )
        chunks = split_pdf_into_chunks(
            pdf_bytes=pdf_bytes,
            max_pages_per_chunk=max_pages_per_chunk,
            chunk_size_limit_mb=chunk_size_limit_mb,
            compression_target_mb=compression_target_mb,
            compression_quality_steps=compression_quality_steps,
        )

        clean_name = sanitize_filename(file_key.split("/")[-1])

        # Step 3: Extract text, chunk, and embed
        logger.info(
            "[3/3] Processing %d chunks (fitz -> %s)...", len(chunks), embeddings_model
        )
        vectors_prepared: list[dict[str, Any]] = []

        vectors_failed = 0

        for idx, (start_page, end_page, chunk_bytes) in enumerate(chunks):
            chunk_key = f"{clean_name}-chunk-{idx}"
            logger.info(
                "  Chunk %d/%d | pages %d-%d",
                idx + 1,
                len(chunks),
                start_page + 1,
                end_page,
            )

            try:
                # Extract text locally with fitz
                text = extract_text_with_fitz(
                    chunk_bytes, preserve_layout=preserve_layout
                )
                logger.info("    Extracted %d characters", len(text))

                if not text.strip():
                    logger.warning(
                        "    Chunk %d produced empty text, skipping.", idx + 1
                    )
                    vectors_failed += 1
                    continue

                # Split extracted text into embed-ready sub-chunks
                text_chunks = split_text_into_embed_chunks(
                    text=text,
                    strategy=chunk_strategy,
                    max_chars=max_embed_chars,
                    overlap=chunk_overlap,
                    heading_pattern=section_heading_pattern,
                )
                logger.info(
                    "    Split into %d sub-chunk(s) | strategy=%s | max_chars=%d",
                    len(text_chunks),
                    chunk_strategy,
                    max_embed_chars,
                )

                # Embed and store each sub-chunk as an independent vector
                for sub_idx, text_chunk in enumerate(text_chunks):
                    sub_key = (
                        f"{chunk_key}-sub-{sub_idx}"
                        if len(text_chunks) > 1
                        else chunk_key
                    )

                    embedding = embed_text_with_mxbai(
                        text=text_chunk,
                        endpoint=embeddings_endpoint,
                        model=embeddings_model,
                        timeout=embeddings_timeout,
                    )
                    logger.info(
                        "    Sub-chunk %d/%d embedded | %d dims | key=%s",
                        sub_idx + 1,
                        len(text_chunks),
                        len(embedding),
                        sub_key,
                    )

                    vector_data: dict[str, Any] = {
                        "key": sub_key,
                        "data": {"float32": embedding},
                        "metadata": {
                            "source_bucket": bucket_name,
                            "source_key": file_key,
                            "start_page": start_page,
                            "end_page": end_page,
                            "chunk_index": idx,
                            "sub_chunk_index": sub_idx,
                            "chunk_strategy": chunk_strategy,
                            "text_extractor": "fitz",
                            "original_text_length": len(text),
                            "sub_chunk_length": len(text_chunk),
                            "embedding_model": embeddings_model,
                            "dimensions": len(embedding),
                            "created_at": datetime.now(timezone.utc).isoformat(),
                            "text": text_chunk,  # store the actual chunk text for RAG retrieval
                        },
                    }
                    vectors_prepared.append(copy.deepcopy(vector_data))

            except Exception:
                logger.warning(
                    "  Chunk %d/%d FAILED, skipping.",
                    idx + 1,
                    len(chunks),
                    exc_info=True,
                )
                vectors_failed += 1
                continue

        logger.info(
            "  Chunks done | %d succeeded | %d failed | %d vectors prepared",
            len(chunks) - vectors_failed,
            vectors_failed,
            len(vectors_prepared),
        )

        # Store vectors in S3 Vectors
        if not vectors_prepared:
            logger.warning("No vectors to store — all chunks failed or were empty.")
            return []

        if store_vectors:
            logger.info("Storing %d vectors in S3 Vectors...", len(vectors_prepared))
            for batch_start in range(0, len(vectors_prepared), vector_batch_size):
                batch = vectors_prepared[batch_start : batch_start + vector_batch_size]
                try:
                    s3v.put_vectors(
                        vectorBucketName=vector_bucket_name,
                        indexName=index_name,
                        vectors=batch,
                    )
                    logger.info(
                        "  Batch %d stored (%d vectors)",
                        batch_start // vector_batch_size + 1,
                        len(batch),
                    )
                except Exception:
                    logger.error("  Failed to store batch.", exc_info=True)
        else:
            logger.info("store_vectors=False, skipping storage.")

        logger.info(
            "=== COMPLETED: %s | %d/%d chunks processed ===",
            file_key,
            len(vectors_prepared),
            len(chunks),
        )
        return vectors_prepared

    except Exception:
        logger.error("=== TOTAL FAILURE: %s ===", file_key, exc_info=True)
        return []


# ---------------------------------------------------------------------------
# Batch pipeline: multiple folders
# ---------------------------------------------------------------------------


def batch_process_pdfs(
    bucket_name: str,
    folders: list[str],
    vector_bucket_name: str,
    index_name: str,
    s3_client: Any | None = None,
    s3vectors_client: Any | None = None,
    region: str = DEFAULT_REGION,
    max_pages_per_chunk: int = DEFAULT_MAX_PAGES_PER_CHUNK,
    chunk_size_limit_mb: float = DEFAULT_CHUNK_SIZE_LIMIT_MB,
    compression_target_mb: float = DEFAULT_COMPRESSION_TARGET_MB,
    compression_quality_steps: list[int] = DEFAULT_COMPRESSION_QUALITY_STEPS,
    preserve_layout: bool = False,
    chunk_strategy: str = DEFAULT_CHUNK_STRATEGY,
    max_embed_chars: int = DEFAULT_MAX_EMBED_CHARS,
    chunk_overlap: int = DEFAULT_CHUNK_OVERLAP,
    section_heading_pattern: str = SECTION_HEADING_PATTERN,
    embeddings_endpoint: str = DEFAULT_EMBEDDINGS_ENDPOINT,
    embeddings_model: str = DEFAULT_EMBEDDINGS_MODEL,
    embeddings_timeout: int = DEFAULT_EMBEDDINGS_TIMEOUT,
    store_vectors: bool = True,
    vector_batch_size: int = DEFAULT_VECTOR_BATCH_SIZE,
) -> list[dict[str, Any]]:
    """Process multiple PDFs from a list of S3 folders.

    Iterates over each folder, lists available PDFs, and runs the full
    pipeline for each one. S3 clients are created once and reused across
    all files.

    Args:
        bucket_name: Source S3 bucket.
        folders: List of S3 folder prefixes to process.
        vector_bucket_name: Destination S3 Vectors bucket.
        index_name: Index name in S3 Vectors.
        s3_client: Reusable S3 client.
        s3vectors_client: Reusable s3vectors client.
        region: AWS region.
        max_pages_per_chunk: Maximum pages per PDF page-chunk.
        chunk_size_limit_mb: PDF chunk size limit in MB.
        compression_target_mb: Compression target in MB.
        compression_quality_steps: JPEG quality levels for compression.
        preserve_layout: If True, fitz extracts text preserving column/block order.
        chunk_strategy: How to split extracted text before embedding.
            "length" (default) — fixed-size sliding window with overlap.
            "section" — split on document headings.
        max_embed_chars: Maximum characters per embed sub-chunk.
        chunk_overlap: Character overlap for length-based chunking.
        section_heading_pattern: Regex for heading detection in section-based chunking.
        embeddings_endpoint: Base URL of the Ollama embeddings server.
        embeddings_model: Ollama model name for embeddings.
        embeddings_timeout: Request timeout in seconds for embedding calls.
        store_vectors: If False, prepares but does not store vectors.
        vector_batch_size: Storage batch size.

    Returns:
        Cumulative list of all successfully processed vectors.
    """
    # Create AWS clients once and reuse across the entire batch
    s3 = s3_client or boto3.client("s3")
    s3v = s3vectors_client or boto3.client("s3vectors", region_name=region)

    all_vectors: list[dict[str, Any]] = []
    successful = 0
    failed = 0

    logger.info(
        "### BATCH PROCESSING | source: s3://%s | target: %s/%s | strategy: %s | model: %s ###",
        bucket_name,
        vector_bucket_name,
        index_name,
        chunk_strategy,
        embeddings_model,
    )

    for folder in folders:
        pdf_files = list_s3_files(
            bucket_name=bucket_name,
            folder_path=folder,
            extension_filter=".pdf",
            s3_client=s3,
        )

        for file_key in pdf_files:
            logger.info("Processing: %s", file_key)
            vectors = process_pdf_to_vectors(
                bucket_name=bucket_name,
                file_key=file_key,
                vector_bucket_name=vector_bucket_name,
                index_name=index_name,
                s3_client=s3,
                s3vectors_client=s3v,
                region=region,
                max_pages_per_chunk=max_pages_per_chunk,
                chunk_size_limit_mb=chunk_size_limit_mb,
                compression_target_mb=compression_target_mb,
                compression_quality_steps=compression_quality_steps,
                preserve_layout=preserve_layout,
                chunk_strategy=chunk_strategy,
                max_embed_chars=max_embed_chars,
                chunk_overlap=chunk_overlap,
                section_heading_pattern=section_heading_pattern,
                embeddings_endpoint=embeddings_endpoint,
                embeddings_model=embeddings_model,
                embeddings_timeout=embeddings_timeout,
                store_vectors=store_vectors,
                vector_batch_size=vector_batch_size,
            )

            if vectors:
                all_vectors.extend(vectors)
                successful += 1
            else:
                failed += 1

    logger.info(
        "### BATCH COMPLETE | successful: %d | failed: %d | total vectors: %d ###",
        successful,
        failed,
        len(all_vectors),
    )
    return all_vectors


# ---------------------------------------------------------------------------
# Vector index management
# ---------------------------------------------------------------------------


def clear_vector_index(
    vector_bucket_name: str,
    index_name: str,
    s3vectors_client: Any | None = None,
    region: str = DEFAULT_REGION,
    batch_size: int = 100,
    delay_between_batches: float = 0.5,
) -> int:
    """Delete all vectors from an S3 Vectors index.

    Iterates in batches until the index is empty.

    Args:
        vector_bucket_name: S3 Vectors bucket.
        index_name: Name of the index to clear.
        s3vectors_client: Reusable s3vectors client.
        region: AWS region.
        batch_size: Number of vectors to delete per batch (max 100).
        delay_between_batches: Seconds to wait between batches to avoid throttling.

    Returns:
        Total number of vectors deleted.
    """
    s3v = s3vectors_client or boto3.client("s3vectors", region_name=region)
    total_deleted = 0

    logger.info("Clearing index: %s/%s", vector_bucket_name, index_name)

    while True:
        try:
            response = s3v.list_vectors(
                vectorBucketName=vector_bucket_name,
                indexName=index_name,
                maxResults=batch_size,
            )
            vectors = response.get("vectors", [])

            if not vectors:
                logger.info("Index confirmed empty. Total deleted: %d", total_deleted)
                break

            keys = [v["key"] for v in vectors]
            delete_response = s3v.delete_vectors(
                vectorBucketName=vector_bucket_name,
                indexName=index_name,
                keys=keys,
            )

            failures = delete_response.get("failures", [])
            if failures:
                logger.warning("%d deletion failures in this batch.", len(failures))

            deleted_in_batch = len(keys) - len(failures)
            total_deleted += deleted_in_batch
            logger.debug(
                "Batch deleted: %d vectors (total: %d)", deleted_in_batch, total_deleted
            )

            time.sleep(delay_between_batches)

        except Exception:
            logger.error("Error during deletion.", exc_info=True)
            break

    return total_deleted


def verify_vector_index_empty(
    vector_bucket_name: str,
    index_name: str,
    s3vectors_client: Any | None = None,
    region: str = DEFAULT_REGION,
) -> bool:
    """Verify that an S3 Vectors index is completely empty.

    Args:
        vector_bucket_name: S3 Vectors bucket.
        index_name: Name of the index to verify.
        s3vectors_client: Reusable s3vectors client.
        region: AWS region.

    Returns:
        True if the index is empty, False if it still contains vectors.
    """
    s3v = s3vectors_client or boto3.client("s3vectors", region_name=region)

    try:
        response = s3v.list_vectors(
            vectorBucketName=vector_bucket_name,
            indexName=index_name,
            maxResults=1,
        )
        remaining = response.get("vectors", [])
        if remaining:
            logger.warning(
                "Index still contains vectors (sample key: %s)",
                remaining[0].get("key"),
            )
            return False
        logger.info("Index confirmed empty.")
        return True

    except s3v.exceptions.NotFoundException:
        logger.info("Index not found (may have already been deleted).")
        return True
    except Exception:
        logger.error("Error verifying index.", exc_info=True)
        return False
