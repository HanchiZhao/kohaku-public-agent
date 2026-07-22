from __future__ import annotations

from unittest import TestCase

from fastapi.testclient import TestClient

from app.main import create_app


class FrontendServiceStub:
    """Minimal lifecycle stub for frontend route tests."""

    def __init__(self) -> None:
        self._started = False

    @property
    def is_started(self) -> bool:
        return self._started

    async def start(self) -> None:
        self._started = True

    async def close(self) -> None:
        self._started = False


class FrontendRouteTests(TestCase):
    """Verify that the local development frontend is served."""

    def setUp(self) -> None:
        self.service = FrontendServiceStub()

        self.application = create_app(
            service_factory=lambda: self.service  # type: ignore[arg-type]
        )

        self.client = TestClient(self.application)
        self.client.__enter__()

    def tearDown(self) -> None:
        self.client.__exit__(None, None, None)

    def test_index_page_is_available(self) -> None:
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(
            response.headers["content-type"].startswith(
                "text/html"
            )
        )
        self.assertIn(
            "Kohaku Public Agent",
            response.text,
        )
        self.assertIn(
            'id="composerForm"',
            response.text,
        )
        self.assertIn(
            'id="sessionList"',
            response.text,
        )

    def test_stylesheet_is_available(self) -> None:
        response = self.client.get(
            "/static/styles.css"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            ".app-shell",
            response.text,
        )

    def test_javascript_is_available(self) -> None:
        response = self.client.get(
            "/static/app.js"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "consumeSseStream",
            response.text,
        )
        self.assertIn(
            "stopGeneration",
            response.text,
        )

    def test_api_documentation_remains_available(
        self,
    ) -> None:
        response = self.client.get("/docs")

        self.assertEqual(response.status_code, 200)
        self.assertIn(
            "swagger-ui",
            response.text.lower(),
        )