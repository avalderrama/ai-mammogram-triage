"""MCP server exposing the mammogram triage use cases as tools."""
from __future__ import annotations

import base64
import json
import logging
from collections.abc import Awaitable, Callable
from dataclasses import asdict, dataclass
from typing import Annotated, Any, Literal, Protocol, TypeVar

import anyio
from mcp import types
from mcp.server import MCPServer
from mcp.server.mcpserver import Context
from pydantic import Field

from mammo_application.analyses import (AnalysisResultDto, AnalyzeMammogramCommand, HeatmapFormat, ImageQuery,
                                        MammogramCheckDto, MaskedImageDto)
from mammo_application.common import AppError, Result

from .formatting import (DISCLAIMER, analysis_markdown, analysis_payload, check_markdown, heatmap_images,
                         masked_regions_text)
from .inputs import ImageInputError, image_from_arguments

logger = logging.getLogger("mammo_mcp")

T = TypeVar("T")

READ_ONLY = types.ToolAnnotations(read_only_hint=True, destructive_hint=False, idempotent_hint=True,
                                  open_world_hint=False)


class AnalyzeHandler(Protocol):
    def handle(self, command: AnalyzeMammogramCommand) -> Result[AnalysisResultDto]: ...


class CheckHandler(Protocol):
    def handle(self, query: ImageQuery) -> Result[MammogramCheckDto]: ...


class MaskHandler(Protocol):
    def handle(self, query: ImageQuery) -> Result[MaskedImageDto]: ...


@dataclass(frozen=True)
class McpHandlers:
    analyze: AnalyzeHandler
    check: CheckHandler
    mask: MaskHandler


@dataclass(frozen=True)
class McpOptions:
    allow_local_paths: bool
    max_image_bytes: int
    version: str = "0.1.0"
    # Requests allowed in flight (running + waiting). Inference is serialized anyway; beyond this the server
    # answers "server_busy" instead of piling decoded images up in memory.
    max_pending_requests: int = 3


ImageBase64 = Annotated[str | None, Field(
    default=None, description="Image as base64 (optionally a data URL 'data:image/png;base64,...'). "
                              "PNG, JPEG, WebP, TIFF or BMP.")]
ImagePath = Annotated[str | None, Field(
    default=None, description="Absolute path of a local image file. Only when the server runs locally (stdio).")]
PanelCount = Annotated[int | None, Field(
    default=None, ge=1, le=8, description="Number of side-by-side views in the image. Omit to detect separators.")]
ResponseFormat = Annotated[Literal["markdown", "json"], Field(
    default="markdown", description="'markdown' for a readable summary, 'json' for the raw result.")]
SummaryLanguage = Annotated[Literal["en", "es"], Field(
    default="en", description="Language of the readable summary: 'en' (default) or 'es' (Spanish). "
                              "Structured content is always in English.")]


def _error(code: str, message: str, details: dict | None = None) -> types.CallToolResult:
    """Errors raised by this server carry a machine-readable code in structured content (same contract for all
    tools). Argument-schema errors are raised by the MCP SDK before the tool runs and carry no code."""
    return types.CallToolResult(is_error=True, content=[types.TextContent(type="text", text=message)],
                                structured_content={"error": code, "message": message, "details": details})


def _app_error(error: AppError) -> types.CallToolResult:
    return _error(error.code.value, error.message, error.details)


class _ServerBusy(Exception):
    pass


def _requester(ctx: Context | None) -> str | None:
    """The Cloudflare Access identity the HTTP layer verified for this request (None over stdio or with the API key).
    Only used to count distinct people per day; never logged or returned."""
    try:
        request = ctx.request_context.request if ctx is not None else None
    except ValueError:  # no request context (called outside a request)
        return None
    identity = getattr(getattr(request, "state", None), "identity", None)
    return identity if isinstance(identity, str) else None


def create_mcp_server(handlers: McpHandlers, options: McpOptions) -> MCPServer:
    mcp = MCPServer(
        name="mammogram_triage_mcp",
        version=options.version,
        instructions=("Triage of mammograms (CC/MLO views) into BI-RADS 1-2 / 3 / 4-5 with a priority level per "
                      "breast (high_priority, review, no_suspicious_findings). Personal data drawn over the image "
                      "is masked first, and non-mammogram images are rejected. Research use only: it never "
                      "replaces the radiologist's reading. " + DISCLAIMER),
    )
    analyze_handler, check_handler, mask_handler = handlers.analyze, handlers.check, handlers.mask
    pending = 0
    worker = anyio.CapacityLimiter(1)

    async def run(fn: Callable[[Any], Result[T]], arg: Any) -> Result[T]:
        nonlocal pending
        if pending >= options.max_pending_requests:
            raise _ServerBusy()
        pending += 1
        try:
            return await anyio.to_thread.run_sync(fn, arg, limiter=worker)
        finally:
            pending -= 1

    async def guarded(tool: str, work: Callable[[], Awaitable[types.CallToolResult]]) -> types.CallToolResult:
        """Input errors, overload and unexpected failures all become coded, actionable tool errors."""
        try:
            return await work()
        except ImageInputError as exc:
            return _error(exc.code, str(exc))
        except _ServerBusy:
            return _error("server_busy", "The server is busy with other analyses. Try again in a few seconds.")
        except Exception:
            # Never log the arguments: they contain the image. The traceback is enough to investigate.
            logger.exception("Unexpected failure in %s", tool)
            return _error("internal_error", "The analysis failed unexpectedly. Try again; if it persists, "
                                            "contact the pilot administrator.")

    def load(image_base64: str | None, image_path: str | None):
        return image_from_arguments(image_base64, image_path, options.allow_local_paths, options.max_image_bytes)

    @mcp.tool(name="mammo_analyze_mammogram", title="Analyze mammogram", annotations=READ_ONLY)
    async def mammo_analyze_mammogram(
        image_base64: ImageBase64 = None, image_path: ImagePath = None, panel_count: PanelCount = None,
        sides: Annotated[list[Literal["R", "L", "unknown"]] | None, Field(
            default=None, description="Breast side of each panel, left to right, e.g. ['R','R'].")] = None,
        views: Annotated[list[Literal["CC", "MLO", "unknown"]] | None, Field(
            default=None, description="View of each panel, left to right, e.g. ['CC','MLO'].")] = None,
        include_heatmap: Annotated[bool, Field(
            default=True, description="Return the heatmap of each view when resolution allows (>=1500 px): one "
            "image, or two with heatmap_format 'layers'.")] = True,
        heatmap_format: Annotated[HeatmapFormat, Field(
            default="combined", description="'combined': one image per view, the red areas drawn over the breast. "
            "'layers': two images per view, the processed breast and then the red areas alone on a transparent "
            "PNG of the same size, so a viewer can show or hide them.")] = "combined",
        response_format: ResponseFormat = "markdown",
        language: SummaryLanguage = "en",
        ctx: Context | None = None,
    ) -> types.CallToolResult:
        """Mask personal data, reject non-mammograms and triage each breast of a mammogram image.

        Returns per breast: level (high_priority / review / no_suspicious_findings), BI-RADS group
        probabilities, quality warnings and, for high-resolution images, a heatmap of suspicious areas.
        Panels of the same side are combined (e.g. RCC + RMLO). Errors carry a code (structuredContent.error)
        and explain how to fix the input.
        """
        async def work() -> types.CallToolResult:
            command = AnalyzeMammogramCommand(load(image_base64, image_path), panel_count,
                                              tuple(sides) if sides else None, tuple(views) if views else None,
                                              include_heatmap, heatmap_format, _requester(ctx))
            result = await run(analyze_handler.handle, command)
            if not result.is_success:
                return _app_error(result.error)
            payload = analysis_payload(result.value, heatmap_format)
            text = (analysis_markdown(result.value, language) if response_format == "markdown"
                    else json.dumps(payload, indent=2))
            content: list = [types.TextContent(type="text", text=text)]
            # In the order of the views with has_heatmap; with layers, each view's breast and then its red areas.
            content += [types.ImageContent(type="image", data=base64.b64encode(image.data).decode(),
                                           mime_type=image.media_type)
                        for breast in result.value.breasts for view in breast.views
                        for image in heatmap_images(view, heatmap_format)]
            return types.CallToolResult(content=content, structured_content=payload)
        return await guarded("mammo_analyze_mammogram", work)

    @mcp.tool(name="mammo_check_is_mammogram", title="Check if image is a mammogram", annotations=READ_ONLY)
    async def mammo_check_is_mammogram(image_base64: ImageBase64 = None, image_path: ImagePath = None,
                                       panel_count: PanelCount = None,
                                       response_format: ResponseFormat = "markdown",
                                       language: SummaryLanguage = "en") -> types.CallToolResult:
        """Tell, per panel, whether the image is a usable 2D mammogram (CC/MLO) and report photo quality
        issues. Use it before asking a doctor to retake a photo. Does not classify BI-RADS."""
        async def work() -> types.CallToolResult:
            result = await run(check_handler.handle, ImageQuery(load(image_base64, image_path), panel_count))
            if not result.is_success:
                return _app_error(result.error)
            payload = asdict(result.value)
            text = (check_markdown(result.value, language) if response_format == "markdown"
                    else json.dumps(payload, indent=2))
            return types.CallToolResult(content=[types.TextContent(type="text", text=text)],
                                        structured_content=payload)
        return await guarded("mammo_check_is_mammogram", work)

    @mcp.tool(name="mammo_mask_personal_data", title="Mask personal data", annotations=READ_ONLY)
    async def mammo_mask_personal_data(image_base64: ImageBase64 = None, image_path: ImagePath = None,
                                       language: SummaryLanguage = "en") -> types.CallToolResult:
        """Cover every text region drawn over a medical image (names, IDs, dates, ages...) and return the
        masked image as PNG. The text that is read is never returned or stored, only counts by category.
        Very faint or very large text may be missed: review the result before sharing it."""
        async def work() -> types.CallToolResult:
            result = await run(mask_handler.handle, ImageQuery(load(image_base64, image_path)))
            if not result.is_success:
                return _app_error(result.error)
            r = result.value
            return types.CallToolResult(
                content=[types.TextContent(type="text", text=masked_regions_text(r.masked_regions, language) + "."),
                         types.ImageContent(type="image", data=base64.b64encode(r.image.data).decode(),
                                            mime_type=r.image.media_type)],
                structured_content={"masked_regions": r.masked_regions, "region_categories": r.region_categories})
        return await guarded("mammo_mask_personal_data", work)

    return mcp
