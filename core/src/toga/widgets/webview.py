from __future__ import annotations

import asyncio

from toga.handlers import AsyncResult, wrapped_handler

from .base import Widget


class JavaScriptResult(AsyncResult):
    RESULT_TYPE = "JavaScript"


class WebView(Widget):
    def __init__(
        self,
        id=None,
        style=None,
        url: str | None = None,
        user_agent: str | None = None,
        on_webview_load: callable | None = None,
    ):
        """Create a new WebView widget.

        Inherits from :class:`~toga.widgets.base.Widget`.

        :param id: The ID for the widget.
        :param style: A style object. If no style is provided, a default style
            will be applied to the widget.
        :param url: The full URL to load in the WebView. If not provided,
            an empty page will be displayed.
        :param user_agent: The user agent to use for web requests. If not
            provided, the default user agent for the platform will be used.
        :param on_webview_load: A handler that will be invoked when the web view
            finishes loading.
        """

        super().__init__(id=id, style=style)

        self._impl = self.factory.WebView(interface=self)
        self.user_agent = user_agent

        # Set the load handler before loading the first URL.
        self.on_webview_load = on_webview_load
        self.url = url

    def _set_url(self, url, future):
        # Utility method for validating and setting the URL with a future.
        if (url is not None) and not (
            url.startswith("https://") or url.startswith("http://")
        ):
            raise ValueError("WebView can only display http:// and https:// URLs")

        self._impl.set_url(url, future=future)

    @property
    def url(self) -> str | None:
        """The current URL.

        WebView can only display ``http://`` and ``https://`` URLs.

        Returns ``None`` if no URL is currently displayed.
        """
        return self._impl.get_url()

    @url.setter
    def url(self, value):
        self._set_url(value, future=None)

    async def load_url(self, url: str):
        """Load a URL, and (except on Android) wait until the loading has completed.

        :param url: The URL to load.
        """
        loop = asyncio.get_event_loop()
        loaded_future = loop.create_future()
        self._set_url(url, future=loaded_future)
        return await loaded_future

    @property
    def on_webview_load(self) -> callable:
        """The handler to invoke when the web view finishes loading. This is not
        currently supported on Android."""
        return self._on_webview_load

    @on_webview_load.setter
    def on_webview_load(self, handler):
        self._on_webview_load = wrapped_handler(self, handler)

    @property
    def user_agent(self) -> str:
        """The user agent to use for web requests."""
        return self._impl.get_user_agent()

    @user_agent.setter
    def user_agent(self, value):
        self._impl.set_user_agent(value)

    def set_content(self, root_url: str, content: str):
        """Set the HTML content of the WebView.

        :param root_url: A URL which will be returned by the ``url`` property, and used
            to resolve any relative URLs in the content. On Windows, this argument is
            not currently supported, and calling this method will set the ``url``
            property to ``None``.
        :param content: The HTML content for the WebView
        """
        self._impl.set_content(root_url, content)

    def evaluate_javascript(self, javascript, on_result=None) -> JavaScriptResult:
        """Evaluate a JavaScript expression.

        **This method is asynchronous**. It does not guarantee that the provided
        JavaScript has finished evaluating when the method returns. The object
        returned by this method can be awaited to obtain the value of the expression,
        or you can provide an ``on_result`` callback.

        :param javascript: The JavaScript expression to evaluate.
        :param on_result: A callback that will be invoked when the JavaScript completes.
            It should take one positional argument, which is the value of the
            expression.

            If evaluation fails, the positional argument will be ``None``, and (except
            on Android and Windows) a keyword argument ``exception`` will be passed with
            an exception object.
        """
        return self._impl.evaluate_javascript(javascript, on_result=on_result)
