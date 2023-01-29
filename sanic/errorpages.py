"""
Sanic `provides a pattern
<https://sanicframework.org/guide/best-practices/exceptions.html#using-sanic-exceptions>`_
for providing a response when an exception occurs. However, if you do no handle
an exception, it will provide a fallback. There are three fallback types:

- HTML - *default*
- Text
- JSON

Setting ``app.config.FALLBACK_ERROR_FORMAT = "auto"`` will enable a switch that
will attempt to provide an appropriate response format based upon the
request type.
"""

import sys
import typing as t

from functools import partial
from traceback import extract_tb

from sanic.exceptions import BadRequest, SanicException
from sanic.helpers import STATUS_CODES
from sanic.log import deprecation
from sanic.request import Request
from sanic.response import HTTPResponse, html, json, text


dumps: t.Callable[..., str]
try:
    from ujson import dumps

    dumps = partial(dumps, escape_forward_slashes=False)
except ImportError:  # noqa
    from json import dumps


DEFAULT_FORMAT = "auto"
FALLBACK_TEXT = (
    "The server encountered an internal error and "
    "cannot complete your request."
)
FALLBACK_STATUS = 500


class BaseRenderer:
    """
    Base class that all renderers must inherit from.
    """

    dumps = staticmethod(dumps)

    def __init__(self, request, exception, debug):
        self.request = request
        self.exception = exception
        self.debug = debug

    @property
    def headers(self):
        if isinstance(self.exception, SanicException):
            return getattr(self.exception, "headers", {})
        return {}

    @property
    def status(self):
        if isinstance(self.exception, SanicException):
            return getattr(self.exception, "status_code", FALLBACK_STATUS)
        return FALLBACK_STATUS

    @property
    def text(self):
        if self.debug or isinstance(self.exception, SanicException):
            return str(self.exception)
        return FALLBACK_TEXT

    @property
    def title(self):
        status_text = STATUS_CODES.get(self.status, b"Error Occurred").decode()
        return f"{self.status} — {status_text}"

    def render(self) -> HTTPResponse:
        """
        Outputs the exception as a :class:`HTTPResponse`.

        :return: The formatted exception
        :rtype: str
        """
        output = (
            self.full
            if self.debug and not getattr(self.exception, "quiet", False)
            else self.minimal
        )
        return output()

    def minimal(self) -> HTTPResponse:  # noqa
        """
        Provide a formatted message that is meant to not show any sensitive
        data or details.
        """
        raise NotImplementedError

    def full(self) -> HTTPResponse:  # noqa
        """
        Provide a formatted message that has all details and is mean to be used
        primarily for debugging and non-production environments.
        """
        raise NotImplementedError


class HTMLRenderer(BaseRenderer):
    """
    Render an exception as HTML.

    The default fallback type.
    """

    TRACEBACK_STYLE = """
        html { font-family: sans-serif }
        h2 { color: #888; }
        .tb-wrapper p, dl, dd { margin: 0 }
        .frame-border { margin: 1rem }
        .frame-line > *, dt, dd { padding: 0.3rem 0.6rem }
        .frame-line, dl { margin-bottom: 0.3rem }
        .frame-code, dd { font-size: 16px; padding-left: 4ch }
        .tb-wrapper, dl { border: 1px solid #eee }
        .tb-header,.obj-header {
            background: #eee; padding: 0.3rem; font-weight: bold
        }
        .frame-descriptor, dt { background: #e2eafb; font-size: 14px }
    """
    TRACEBACK_WRAPPER_HTML = (
        "<div class=tb-header>{exc_name}: {exc_value}</div>"
        "<div class=tb-wrapper>{frame_html}</div>"
    )
    TRACEBACK_BORDER = (
        "<div class=frame-border>"
        "The above exception was the direct cause of the following exception:"
        "</div>"
    )
    TRACEBACK_LINE_HTML = (
        "<div class=frame-line>"
        "<p class=frame-descriptor>"
        "File {0.filename}, line <i>{0.lineno}</i>, "
        "in <code><b>{0.name}</b></code>"
        "<p class=frame-code><code>{0.line}</code>"
        "</div>"
    )
    OBJECT_WRAPPER_HTML = (
        "<div class=obj-header>{title}</div>"
        "<dl class={obj_type}>{display_html}</dl>"
    )
    OBJECT_DISPLAY_HTML = "<dt>{key}</dt><dd><code>{value}</code></dd>"
    OUTPUT_HTML = (
        "<!DOCTYPE html><html lang=en>"
        "<meta charset=UTF-8><title>{title}</title>\n"
        "<style>{style}</style>\n"
        "<h1>{title}</h1><p>{text}\n"
        "{body}"
    )

    def full(self) -> HTTPResponse:
        return html(
            self.OUTPUT_HTML.format(
                title=self.title,
                text=self.text,
                style=self.TRACEBACK_STYLE,
                body=self._generate_body(full=True),
            ),
            status=self.status,
        )

    def minimal(self) -> HTTPResponse:
        return html(
            self.OUTPUT_HTML.format(
                title=self.title,
                text=self.text,
                style=self.TRACEBACK_STYLE,
                body=self._generate_body(full=False),
            ),
            status=self.status,
            headers=self.headers,
        )

    @property
    def text(self):
        return escape(super().text)

    @property
    def title(self):
        return escape(f"⚠️ {super().title}")

    def _generate_body(self, *, full):
        lines = []
        if full:
            _, exc_value, __ = sys.exc_info()
            exceptions = []
            while exc_value:
                exceptions.append(self._format_exc(exc_value))
                exc_value = exc_value.__cause__

            traceback_html = self.TRACEBACK_BORDER.join(reversed(exceptions))
            appname = escape(self.request.app.name)
            name = escape(self.exception.__class__.__name__)
            value = escape(self.exception)
            path = escape(self.request.path)
            lines += [
                f"<h2>Traceback of {appname} " "(most recent call last):</h2>",
                f"{traceback_html}",
                "<div class=summary><p>",
                f"<b>{name}: {value}</b> "
                f"while handling path <code>{path}</code>",
                "</div>",
            ]

        for attr, display in (("context", True), ("extra", bool(full))):
            info = getattr(self.exception, attr, None)
            if info and display:
                lines.append(self._generate_object_display(info, attr))

        return "\n".join(lines)

    def _generate_object_display(
        self, obj: t.Dict[str, t.Any], descriptor: str
    ) -> str:
        display = "".join(
            self.OBJECT_DISPLAY_HTML.format(key=key, value=value)
            for key, value in obj.items()
        )
        return self.OBJECT_WRAPPER_HTML.format(
            title=descriptor.title(),
            display_html=display,
            obj_type=descriptor.lower(),
        )

    def _format_exc(self, exc):
        frames = extract_tb(exc.__traceback__)
        frame_html = "".join(
            self.TRACEBACK_LINE_HTML.format(frame) for frame in frames
        )
        return self.TRACEBACK_WRAPPER_HTML.format(
            exc_name=escape(exc.__class__.__name__),
            exc_value=escape(exc),
            frame_html=frame_html,
        )


class TextRenderer(BaseRenderer):
    """
    Render an exception as plain text.
    """

    OUTPUT_TEXT = "{title}\n{bar}\n{text}\n\n{body}"
    SPACER = "  "

    def full(self) -> HTTPResponse:
        return text(
            self.OUTPUT_TEXT.format(
                title=self.title,
                text=self.text,
                bar=("=" * len(self.title)),
                body=self._generate_body(full=True),
            ),
            status=self.status,
        )

    def minimal(self) -> HTTPResponse:
        return text(
            self.OUTPUT_TEXT.format(
                title=self.title,
                text=self.text,
                bar=("=" * len(self.title)),
                body=self._generate_body(full=False),
            ),
            status=self.status,
            headers=self.headers,
        )

    @property
    def title(self):
        return f"⚠️ {super().title}"

    def _generate_body(self, *, full):
        lines = []
        if full:
            _, exc_value, __ = sys.exc_info()
            exceptions = []

            lines += [
                f"{self.exception.__class__.__name__}: {self.exception} while "
                f"handling path {self.request.path}",
                f"Traceback of {self.request.app.name} "
                "(most recent call last):\n",
            ]

            while exc_value:
                exceptions.append(self._format_exc(exc_value))
                exc_value = exc_value.__cause__

            lines += exceptions[::-1]

        for attr, display in (("context", True), ("extra", bool(full))):
            info = getattr(self.exception, attr, None)
            if info and display:
                lines += self._generate_object_display_list(info, attr)

        return "\n".join(lines)

    def _format_exc(self, exc):
        frames = "\n\n".join(
            [
                f"{self.SPACER * 2}File {frame.filename}, "
                f"line {frame.lineno}, in "
                f"{frame.name}\n{self.SPACER * 2}{frame.line}"
                for frame in extract_tb(exc.__traceback__)
            ]
        )
        return f"{self.SPACER}{exc.__class__.__name__}: {exc}\n{frames}"

    def _generate_object_display_list(self, obj, descriptor):
        lines = [f"\n{descriptor.title()}"]
        for key, value in obj.items():
            display = self.dumps(value)
            lines.append(f"{self.SPACER * 2}{key}: {display}")
        return lines


class JSONRenderer(BaseRenderer):
    """
    Render an exception as JSON.
    """

    def full(self) -> HTTPResponse:
        output = self._generate_output(full=True)
        return json(output, status=self.status, dumps=self.dumps)

    def minimal(self) -> HTTPResponse:
        output = self._generate_output(full=False)
        return json(output, status=self.status, dumps=self.dumps)

    def _generate_output(self, *, full):
        output = {
            "description": self.title,
            "status": self.status,
            "message": self.text,
        }

        for attr, display in (("context", True), ("extra", bool(full))):
            info = getattr(self.exception, attr, None)
            if info and display:
                output[attr] = info

        if full:
            _, exc_value, __ = sys.exc_info()
            exceptions = []

            while exc_value:
                exceptions.append(
                    {
                        "type": exc_value.__class__.__name__,
                        "exception": str(exc_value),
                        "frames": [
                            {
                                "file": frame.filename,
                                "line": frame.lineno,
                                "name": frame.name,
                                "src": frame.line,
                            }
                            for frame in extract_tb(exc_value.__traceback__)
                        ],
                    }
                )
                exc_value = exc_value.__cause__

            output["path"] = self.request.path
            output["args"] = self.request.args
            output["exceptions"] = exceptions[::-1]

        return output

    @property
    def title(self):
        return STATUS_CODES.get(self.status, b"Error Occurred").decode()


def escape(text):
    """
    Minimal HTML escaping, not for attribute values (unlike html.escape).
    """
    return f"{text}".replace("&", "&amp;").replace("<", "&lt;")


RENDERERS_BY_CONFIG = {
    "html": HTMLRenderer,
    "json": JSONRenderer,
    "text": TextRenderer,
}

RENDERERS_BY_CONTENT_TYPE = {
    "text/plain": TextRenderer,
    "application/json": JSONRenderer,
    "multipart/form-data": HTMLRenderer,
    "text/html": HTMLRenderer,
}
CONTENT_TYPE_BY_RENDERERS = {
    v: k for k, v in RENDERERS_BY_CONTENT_TYPE.items()
}
# Handler source code is checked for which response types it returns
# If it returns (exactly) one of these, it will be used as render_format
RESPONSE_MAPPING = {
    "empty": "html",
    "json": "json",
    "text": "text",
    "raw": "text",
    "html": "html",
    "file": "html",
    "file_stream": "text",
    "stream": "text",
    "redirect": "html",
    "text/plain": "text",
    "text/html": "html",
    "application/json": "json",
}


def check_error_format(format):
    if format not in RENDERERS_BY_CONFIG and format != "auto":
        raise SanicException(f"Unknown format: {format}")


def exception_response(
    request: Request,
    exception: Exception,
    debug: bool,
    fallback: str,
    base: t.Type[BaseRenderer],
    renderer: t.Type[t.Optional[BaseRenderer]] = None,
) -> HTTPResponse:
    """
    Render a response for the default FALLBACK exception handler.
    """
    if not renderer:
        renderer = _guess_renderer(request, fallback, base)

    renderer = t.cast(t.Type[BaseRenderer], renderer)
    return renderer(request, exception, debug).render()

def _acceptable(req, mediatype):
    # Check if the given type/subtype is an acceptable response
    # TODO: Consider defaulting req.accept to */*:q=0 when there is no
    #       accept header at all, to allow simply using match().
    return not req.accept or req.accept.match(mediatype)

def _guess_renderer(req: Request, fallback: str, base: t.Type[BaseRenderer]) -> t.Type[BaseRenderer]:
    # Renderer selection order:
    # 1. Accept header (ignoring */* or types with q=0)
    # 2. Route error_format
    # 3. FALLBACK if set by app
    # 4. Content-type for JSON
    #
    # If none of the above match or are in conflict with accept header,
    # then the base renderer is returned.
    #
    # Arguments:
    # - fallback is auto/json/html/text (app.config.FALLBACK_ERROR_FORMAT)
    # - base is always TextRenderer unless set via
    #   Sanic(error_handler=ErrorRenderer(SomeOtherRenderer))

    # Use the Accept header preference to choose one of the renderers
    mediatype, accept_q = req.accept.choose(*RENDERERS_BY_CONTENT_TYPE)
    if accept_q:
        return RENDERERS_BY_CONTENT_TYPE[mediatype]

    # No clear preference, so employ fuzzy logic to find render_format
    render_format = fallback

    # Check the route for what the handler returns (magic)
    # Note: this is done despite having a non-auto fallback
    if req.route:
        try:
            if req.route.extra.error_format:
                render_format = req.route.extra.error_format
        except AttributeError:
            pass

    # If still not known, check for JSON content-type
    if render_format == "auto":
        mediatype = req.headers.getone("content-type", "").split(";", 1)[0]
        if mediatype == "application/json":
            render_format = "json"

    # Check for JSON body content (DEPRECATED, backwards compatibility)
    if render_format == "auto" and _acceptable(req, "application/json"):
        try:
            if req.json:
                render_format = "json"
                deprecation(
                    "Response type was determined by the JSON content of "
                    "the request. This behavior is deprecated and will be "
                    "removed in v24.3. Please specify the format either by\n"
                    "  FALLBACK_ERROR_FORMAT = 'json', or by adding header\n"
                    "  accept: application/json to your requests.",
                    24.3,
                )
        except Exception:
            pass

    # Use render_format if found and acceptable, otherwise fallback to base
    renderer = RENDERERS_BY_CONFIG.get(render_format, base)
    mediatype = CONTENT_TYPE_BY_RENDERERS[renderer]  # type: ignore
    return renderer if _acceptable(req, mediatype) else base
