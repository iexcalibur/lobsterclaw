"""
Media tools — mirrors OpenClaw's pdf, image, and tts tools.

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

# Injected by main.py after Telegram is ready
_send_audio_fn: Callable[[str], Awaitable[None]] | None = None


def set_send_audio(fn: Callable[[str], Awaitable[None]]) -> None:
    global _send_audio_fn
    _send_audio_fn = fn


PDF_TOOL = ToolDefinition(
    name="pdf",
    description="Read and extract text from a PDF file (local path or URL).",
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Local file path (~/ supported) or HTTPS URL"},
            "pages": {"type": "string", "description": "Page range e.g. '1-5' or single page '3' (optional, all pages if omitted)"},
        },
        "required": ["source"],
    },
    fn=lambda **kw: _pdf(**kw),
)

IMAGE_TOOL = ToolDefinition(
    name="image",
    description="Analyze or describe an image file (local path or URL). Uses vision-capable model.",
    parameters={
        "type": "object",
        "properties": {
            "source": {"type": "string", "description": "Local file path (~/ supported) or HTTPS URL"},
            "question": {"type": "string", "description": "What to ask about the image (default: describe it)"},
        },
        "required": ["source"],
    },
    fn=lambda **kw: _image(**kw),
)

TTS_TOOL = ToolDefinition(
    name="tts",
    description=(
        "Convert text to speech and send it as a Telegram voice message. "
        "Requires TTS_ENABLED=true."
    ),
    parameters={
        "type": "object",
        "properties": {
            "text": {"type": "string", "description": "Text to convert to speech"},
            "voice": {"type": "string", "description": "Edge TTS voice name (optional, uses TTS_VOICE from .env)"},
        },
        "required": ["text"],
    },
    fn=lambda **kw: _tts(**kw),
)


async def _pdf(source: str, pages: str | None = None) -> str:
    tmp_path: str | None = None
    try:
        import fitz  # PyMuPDF

        # Download if URL
        if source.startswith("http"):
            import httpx
            async with httpx.AsyncClient(timeout=30, follow_redirects=True) as client:
                r = await client.get(source)
                r.raise_for_status()
            fd, tmp_path = tempfile.mkstemp(suffix=".pdf")
            os.close(fd)
            with open(tmp_path, "wb") as f:
                f.write(r.content)
            source = tmp_path

        doc = fitz.open(Path(source).expanduser())
        total_pages = len(doc)

        # Parse page range — 1-indexed
        start, end = 0, total_pages
        if pages:
            parts = pages.split("-")
            try:
                start = max(0, int(parts[0]) - 1)
                end = int(parts[1]) if len(parts) > 1 else int(parts[0])
            except ValueError:
                pass  # fallback to all pages

        text_parts = []
        for i in range(start, min(end, total_pages)):
            text_parts.append(f"--- Page {i + 1} ---\n{doc[i].get_text()}")

        doc.close()
        combined = "\n\n".join(text_parts)
        return combined[:10_000] + ("\n\n[truncated]" if len(combined) > 10_000 else "")
    except ImportError:
        return "Error: PyMuPDF not installed. Run: pip install PyMuPDF"
    except Exception as e:
        return f"Error reading PDF: {e}"
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


async def _image(source: str, question: str = "Describe this image in detail.") -> str:
    cfg = get_config()
    tmp_path: str | None = None

    try:
        import httpx

        # Load image bytes
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

        b64 = base64.standard_b64encode(image_bytes).decode()

        if cfg.llm_provider == "anthropic":
            import anthropic
            client = anthropic.AsyncAnthropic(api_key=cfg.anthropic_api_key)
            response = await client.messages.create(
                model=cfg.llm_model,
                max_tokens=1024,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image", "source": {"type": "base64", "media_type": media_type, "data": b64}},
                        {"type": "text", "text": question},
                    ],
                }],
            )
            return response.content[0].text
        else:
            import openai
            client = openai.AsyncOpenAI(api_key=cfg.openai_api_key)
            response = await client.chat.completions.create(
                model=cfg.llm_model,
                messages=[{
                    "role": "user",
                    "content": [
                        {"type": "image_url", "image_url": {"url": f"data:{media_type};base64,{b64}"}},
                        {"type": "text", "text": question},
                    ],
                }],
                max_tokens=1024,
            )
            return response.choices[0].message.content or "(no response)"
    except ImportError:
        return "Error: required library not installed (httpx)"
    except Exception as e:
        return f"Error analyzing image: {e}"
    finally:
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


async def _tts(text: str, voice: str | None = None) -> str:
    cfg = get_config()
    if not cfg.tts_enabled:
        return "TTS is disabled (TTS_ENABLED=false)"

    tmp_path: str | None = None
    try:
        import edge_tts

        voice = voice or cfg.tts_voice
        communicate = edge_tts.Communicate(text, voice)

        fd, tmp_path = tempfile.mkstemp(suffix=".mp3")
        os.close(fd)
        await communicate.save(tmp_path)

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
