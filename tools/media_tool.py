"""
Media tools — mirrors OpenClaw's pdf, image, and tts tools.

Field parity with OpenClaw:
  pdf:   pdf/source (alias), prompt, pages, model, maxBytesMb, pdfs[] (multi-file)
  image: image/source (alias), prompt/question (alias), images[] (multi), model, maxBytesMb, maxImages
  tts:   text, channel, voice (extra)

All temp files are cleaned up after use.
"""

from __future__ import annotations

import base64
import logging
import os
import tempfile
from pathlib import Path
from typing import Awaitable, Callable

from config import get_config
from tools.registry import ToolDefinition

logger = logging.getLogger(__name__)

_send_audio_fn: Callable[[str], Awaitable[None]] | None = None


def set_send_audio(fn: Callable[[str], Awaitable[None]]) -> None:
    global _send_audio_fn
    _send_audio_fn = fn


# ------------------------------------------------------------------
# pdf tool
# ------------------------------------------------------------------

PDF_TOOL = ToolDefinition(
    name="pdf",
    description=(
        "Read and extract text from one or more PDF files.\n\n"
        "Field parity with OpenClaw pdf-tool.ts:\n"
        "  pdf / source   — single PDF path or URL (also 'pdf' field name)\n"
        "  pdfs[]         — array of PDF paths/URLs for multi-file batch\n"
        "  prompt         — instruction to apply when extracting (e.g. 'summarize')\n"
        "  pages          — page range: '1-5' or single page '3'\n"
        "  model          — override vision model for prompt-based extraction\n"
        "  maxBytesMb     — max file size in MB to process (default 10)"
    ),
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "PDF path or URL (also 'pdf')"},
            "pdf": {"type": "string", "description": "Alias for source (OpenClaw field name)"},
            "pdfs": {
                "type": "array",
                "description": "List of PDF paths/URLs for batch processing",
                "items": {"type": "string"},
            },
            "prompt": {
                "type": "string",
                "description": "Instruction to apply to extracted text (e.g. 'summarize the key points')",
            },
            "pages": {
                "type": "string",
                "description": "Page range: '1-5', single page '3', or omit for all pages",
            },
            "model": {
                "type": "string",
                "description": "Override LLM model for prompt-based extraction",
            },
            "maxBytesMb": {
                "type": "number",
                "description": "Max PDF size in MB to process (default 10)",
                "default": 10,
            },
        },
        "required": [],
    },
    fn=lambda **kw: _pdf(**kw),
)

# ------------------------------------------------------------------
# image tool
# ------------------------------------------------------------------

IMAGE_TOOL = ToolDefinition(
    name="image",
    description=(
        "Analyze one or more images using a vision-capable model.\n\n"
        "Field parity with OpenClaw image-tool.ts:\n"
        "  image / source  — single image path or URL\n"
        "  images[]        — array of image paths/URLs for batch\n"
        "  prompt / question — what to ask about the image(s)\n"
        "  model           — override vision model\n"
        "  maxBytesMb      — max image size in MB per image\n"
        "  maxImages       — max images to process in a batch"
    ),
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Image path or URL (also 'image')"},
            "image": {"type": "string", "description": "Alias for source (OpenClaw field name)"},
            "images": {
                "type": "array",
                "description": "List of image paths/URLs for batch analysis",
                "items": {"type": "string"},
            },
            "prompt": {"type": "string", "description": "Question/instruction (OpenClaw field name; also 'question')"},
            "question": {"type": "string", "description": "Alias for prompt"},
            "model": {
                "type": "string",
                "description": "Override vision model (e.g. 'claude-opus-4-5', 'gpt-4o')",
            },
            "maxBytesMb": {
                "type": "number",
                "description": "Max image size in MB per image (default 5)",
                "default": 5,
            },
            "maxImages": {
                "type": "integer",
                "description": "Max images to include in a batch request (default 5)",
                "default": 5,
            },
        },
        "required": [],
    },
    fn=lambda **kw: _image(**kw),
)

# ------------------------------------------------------------------
# tts tool
# ------------------------------------------------------------------

TTS_TOOL = ToolDefinition(
    name="tts",
    description=(
        "Convert text to speech and send as a Telegram voice message.\n\n"
        "Field parity with OpenClaw tts-tool.ts:\n"
        "  text     — required: text to synthesize\n"
        "  channel  — target channel to send to (OpenClaw field; default: owner Telegram)\n"
        "  voice    — Edge TTS voice name (extra: uses TTS_VOICE from .env if omitted)\n\n"
        "Requires TTS_ENABLED=true."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to convert to speech"},
            "channel": {
                "type": "string",
                "description": "Target channel (OpenClaw field; 'telegram' default, 'discord', etc.)",
            },
            "voice": {
                "type": "string",
                "description": "Edge TTS voice name (e.g. 'en-US-ChristopherNeural')",
            },
            "output_path": {
                "type": "string",
                "description": "Save to file instead of sending (optional)",
            },
        },
        "required": ["text"],
    },
    fn=lambda **kw: _tts(**kw),
)


# ------------------------------------------------------------------
# Implementations
# ------------------------------------------------------------------

async def _pdf(
    source: str | None = None,
    pdf: str | None = None,               # OpenClaw field name
    pdfs: list[str] | None = None,        # multi-file batch
    prompt: str | None = None,
    pages: str | None = None,
    model: str | None = None,
    maxBytesMb: float = 10,
) -> str:
    # Resolve single source
    effective_source = source or pdf
    sources = pdfs or ([effective_source] if effective_source else [])
    if not sources:
        return "Error: 'source' (or 'pdf' or 'pdfs') is required"

    results = []
    for src in sources[:5]:  # cap at 5 per batch
        results.append(await _read_single_pdf(src, pages=pages, max_mb=maxBytesMb))

    combined_text = "\n\n---\n\n".join(results)

    # If prompt provided, run LLM on extracted text
    if prompt:
        return await _apply_prompt_to_text(combined_text, prompt, model=model)

    return combined_text


async def _read_single_pdf(source: str, pages: str | None = None, max_mb: float = 10) -> str:
    tmp_path: str | None = None
    try:
        import fitz  # PyMuPDF

        if source.startswith("http"):
            import httpx
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                r = await client.get(source)
                r.raise_for_status()
            content = r.content
            if len(content) > max_mb * 1024 * 1024:
                return f"Error: PDF too large ({len(content) / 1024 / 1024:.1f} MB > {max_mb} MB limit)"
            fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
            os.close(fd)
            with open(tmp_path, "wb") as f:
                f.write(content)
            source_path = tmp_path
        else:
            source_path = str(Path(source).expanduser())
            size_mb = os.path.getsize(source_path) / 1024 / 1024
            if size_mb > max_mb:
                return f"Error: PDF too large ({size_mb:.1f} MB > {max_mb} MB limit)"

        doc = fitz.open(source_path)
        total = len(doc)
        start, end = 0, total
        if pages:
            parts = pages.split("-")
            try:
                start = max(0, int(parts[0]) - 1)
                end = int(parts[1]) if len(parts) > 1 else int(parts[0])
            except ValueError:
                pass

        text_parts = [f"--- Page {i+1} ---\n{doc[i].get_text()}" for i in range(start, min(end, total))]
        doc.close()
        combined = "\n\n".join(text_parts)
        return combined[:10_000] + ("\n\n[truncated]" if len(combined) > 10_000 else "")
    except ImportError:
        return "Error: PyMuPDF not installed. Run: pip install PyMuPDF"
    except Exception as e:
        return f"Error reading PDF {source}: {e}"
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


async def _image(
    source: str | None = None,
    image: str | None = None,              # OpenClaw field name
    images: list[str] | None = None,       # multi-image batch
    prompt: str | None = None,             # OpenClaw field name
    question: str | None = None,           # alias
    model: str | None = None,
    maxBytesMb: float = 5,
    maxImages: int = 5,
) -> str:
    cfg = get_config()

    effective_source = source or image
    sources = images or ([effective_source] if effective_source else [])
    if not sources:
        return "Error: 'source' (or 'image' or 'images') is required"

    effective_prompt = prompt or question or "Describe this image in detail."
    sources = sources[:maxImages]

    # Build image content blocks
    image_blocks = []
    for src in sources:
        block = await _load_image_block(src, max_mb=maxBytesMb, provider=cfg.llm_provider)
        if isinstance(block, str):  # error string
            return block
        image_blocks.append(block)

    effective_model = model or cfg.llm_model

    try:
        if cfg.llm_provider == "anthropic":
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
            content = [*image_blocks, {"type": "text", "text": effective_prompt}]
            response = await client.messages.create(
                model=effective_model,
                max_tokens=1024,
                messages=[{"role": "user", "content": content}],
            )
            return response.content[0].text
        else:
            import openai
            client = openai.AsyncOpenAI(api_key=cfg.openai_api_key)
            oai_content = [*image_blocks, {"type": "text", "text": effective_prompt}]
            response = await client.chat.completions.create(
                model=effective_model,
                messages=[{"role": "user", "content": oai_content}],
                max_tokens=1024,
            )
            return response.choices[0].message.content or "(no response)"
    except Exception as e:
        return f"Error analyzing image: {e}"


async def _load_image_block(source: str, max_mb: float, provider: str) -> dict | str:
    """Load image as provider-specific content block."""
    try:
        import httpx
        if source.startswith("http"):
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                r = await client.get(source)
                r.raise_for_status()
                image_bytes = r.content
                media_type = r.headers.get("content-type", "image/jpeg").split(";")[0]
        else:
            p = Path(source).expanduser()
            if not p.exists():
                return f"Error: file not found: {source}"
            image_bytes = p.read_bytes()
            suffix = p.suffix.lower().lstrip(".")
            media_type = {
                "jpg": "image/jpeg", "jpeg": "image/jpeg",
                "png": "image/png", "gif": "image/gif", "webp": "image/webp",
            }.get(suffix, "image/jpeg")

        size_mb = len(image_bytes) / 1024 / 1024
        if size_mb > max_mb:
            return f"Error: image too large ({size_mb:.1f} MB > {max_mb} MB): {source}"

        b64 = base64.standard_b64encode(image_bytes).decode()

        if provider == "anthropic":
            return {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}}
        else:
            return {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}}
    except Exception as e:
        return f"Error loading image {source}: {e}"


async def _apply_prompt_to_text(text: str, prompt: str, model: str | None = None) -> str:
    """Run a LLM prompt over extracted text (used for pdf prompt= feature)."""
    cfg = get_config()
    effective_model = model or cfg.llm_model
    try:
        if cfg.llm_provider == "anthropic":
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
            response = await client.messages.create(
                model=effective_model,
                max_tokens=2048,
                messages=[{
                    "role": "user",
                    "content": f"{prompt}\n\n---\n\n{text[:50000]}",
                }],
            )
            return response.content[0].text
        else:
            import openai
            client = openai.AsyncOpenAI(api_key=cfg.openai_api_key)
            response = await client.chat.completions.create(
                model=effective_model,
                messages=[{"role": "user", "content": f"{prompt}\n\n---\n\n{text[:50000]}"}],
                max_tokens=2048,
            )
            return response.choices[0].message.content or "(no response)"
    except Exception as e:
        return f"Error applying prompt to text: {e}\n\nRaw text:\n{text[:3000]}"


async def _tts(
    text: str,
    channel: str | None = None,    # OpenClaw field name
    voice: str | None = None,
    output_path: str | None = None,
) -> str:
    cfg = get_config()
    if not cfg.tts_enabled:
        return "TTS is disabled (TTS_ENABLED=false)"

    tmp_path: str | None = None
    try:
        import edge_tts
        voice = voice or cfg.tts_voice
        communicate = edge_tts.Communicate(text, voice)

        if output_path:
            await communicate.save(output_path)
            return f"Audio saved to {output_path} ✅"

        fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        await communicate.save(tmp_path)

        # channel routing: default is owner Telegram
        if channel and channel not in ("telegram", "default", ""):
            logger.info("TTS channel '%s' not yet wired; defaulting to Telegram", channel)

        if _send_audio_fn:
            await _send_audio_fn(tmp_path)
            return f"Voice message sent ✅ ({len(text)} chars)"
        return f"Audio generated but no Telegram send function configured (file: {tmp_path})"
    except ImportError:
        return "Error: edge-tts not installed. Run: pip install edge-tts"
    except Exception as e:
        return f"Error generating TTS: {e}"
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass
