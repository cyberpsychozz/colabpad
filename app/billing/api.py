"""REST for plans: checkout, the provider's webhook, cancel."""

from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, Response

from app.auth.sessions import ensure_session
from app.errors import AppError
from app.models import CheckoutIn

Session = Annotated[dict[str, Any], Depends(ensure_session)]


def billing_router() -> APIRouter:
    router = APIRouter(prefix="/api/billing")

    @router.post("/checkout")
    async def checkout(body: CheckoutIn, request: Request, session: Session) -> dict[str, str]:
        return {"confirmationUrl": await request.app.state.billing.checkout(session, body.plan)}

    @router.post("/webhook")
    async def webhook(request: Request) -> Response:
        billing = request.app.state.billing
        if not billing.ip_allowed(request.client.host if request.client else None):
            raise AppError("NOT_FOUND")
        try:
            payment_id = (await request.json())["object"]["id"]
        except (ValueError, KeyError, TypeError):
            raise AppError("VALIDATION_FAILED") from None
        if not isinstance(payment_id, str) or len(payment_id) > 64:
            raise AppError("VALIDATION_FAILED")
        await billing.webhook(payment_id)
        return Response(status_code=200)

    @router.post("/cancel")
    async def cancel(request: Request, session: Session) -> dict[str, Any]:
        return {"subscription": request.app.state.billing.cancel(session)}

    return router
