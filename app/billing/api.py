"""REST for plans: checkout, Robokassa's ResultURL, the buyer's payment, cancel."""

from typing import Annotated, Any
from urllib.parse import parse_qsl

from fastapi import APIRouter, Depends, Request
from fastapi.responses import PlainTextResponse

from app.auth.sessions import ensure_session
from app.errors import AppError
from app.models import CheckoutIn

Session = Annotated[dict[str, Any], Depends(ensure_session)]
MAX_RESULT_BYTES = 16_384


def billing_router() -> APIRouter:
    router = APIRouter(prefix="/api/billing")

    @router.post("/checkout")
    async def checkout(body: CheckoutIn, request: Request, session: Session) -> dict[str, str]:
        return {"paymentUrl": await request.app.state.billing.checkout(session, body.plan)}

    # The shop's technical settings choose GET or POST for ResultURL; both are accepted.
    @router.api_route("/result", methods=["GET", "POST"])
    async def result(request: Request) -> PlainTextResponse:
        billing = request.app.state.billing
        if not billing.ip_allowed(request.client.host if request.client else None):
            raise AppError("NOT_FOUND")
        body = await request.body()
        if len(body) > MAX_RESULT_BYTES:
            raise AppError("VALIDATION_FAILED")
        fields = dict(request.query_params)
        fields.update(parse_qsl(body.decode("utf-8", "replace"), keep_blank_values=True))
        return PlainTextResponse(await billing.result(fields))

    @router.get("/payments/{inv_id}")
    async def payment(inv_id: int, request: Request, session: Session) -> dict[str, Any]:
        return request.app.state.billing.payment(session, inv_id)

    @router.post("/cancel")
    async def cancel(request: Request, session: Session) -> dict[str, Any]:
        return {"subscription": request.app.state.billing.cancel(session)}

    return router
