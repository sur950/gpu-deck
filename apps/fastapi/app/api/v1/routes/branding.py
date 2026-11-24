"""
Branding route (admin.yaml: Branding tag). Public, read-only, values from .env.

GET /branding   Cache-Control: public, max-age=300
"""

from app.core.logger import get_logger
from app.core.settings import settings
from app.schemas.branding_schema import Branding
from app.utils.api_wrapper import handle_exceptions, success_response
from fastapi import APIRouter, Request, Response

logger = get_logger(__name__)

router = APIRouter()
TAGS = ["Branding"]


def build_branding() -> Branding:
    return Branding(
        app_name=settings.brand_name,
        app_byline=settings.BRAND_TAGLINE,
        brand_color=settings.BRAND_PRIMARY_COLOR,
        logo_url=settings.BRAND_LOGO_URL,
        favicon_url=settings.BRAND_FAVICON_URL,
        docs_url=settings.BRAND_DOCS_URL or None,
        support_url=settings.BRAND_SUPPORT_URL or None,
        theme={
            "primary_color": settings.BRAND_PRIMARY_COLOR,
            "logo_dark_url": settings.BRAND_LOGO_DARK_URL or settings.BRAND_LOGO_URL,
            "company": settings.BRAND_COMPANY,
            "tagline": settings.BRAND_TAGLINE,
        },
    )


@router.get("/branding", summary="Branding for the login page and UI shell")
@handle_exceptions
async def get_branding(request: Request, response: Response):
    response.headers["Cache-Control"] = "public, max-age=300"
    return success_response(data=build_branding())
