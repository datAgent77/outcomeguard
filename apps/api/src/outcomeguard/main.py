from __future__ import annotations

import logging

from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from outcomeguard.api import routes_agreements, routes_payments, routes_session, routes_system
from outcomeguard.auth import AuthError, Forbidden
from outcomeguard.config import get_settings
from outcomeguard.logs import configure_logging
from outcomeguard.paypal.gateway import PayPalAPIError, PayPalNotConfiguredError
from outcomeguard.services.agreements import AgreementError
from outcomeguard.services.payments import PaymentRuleViolation, PaymentUncertain

log = logging.getLogger("outcomeguard")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(
        title="OutcomeGuard API",
        version="0.1.0",
        description="Evidence-bound payment authorization on PayPal (Sandbox only).",
    )
    app.add_middleware(
        CORSMiddleware,
        allow_origins=[settings.web_url],
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT"],
        allow_headers=["Content-Type", "Authorization", "X-Operator-Token", "Idempotency-Key"],
    )

    @app.exception_handler(PaymentRuleViolation)
    def _rule(_: Request, exc: PaymentRuleViolation) -> JSONResponse:
        return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.http_status)

    @app.exception_handler(PaymentUncertain)
    def _uncertain(_: Request, exc: PaymentUncertain) -> JSONResponse:
        return JSONResponse(
            {
                "code": "OUTCOME_UNKNOWN",
                "message": str(exc),
                "entity": exc.entity,
                "entity_id": exc.entity_id,
            },
            status_code=503,
        )

    @app.exception_handler(PayPalAPIError)
    def _paypal(_: Request, exc: PayPalAPIError) -> JSONResponse:
        return JSONResponse({"code": "PAYPAL_REJECTED", "paypal": exc.to_record()}, status_code=502)

    @app.exception_handler(PayPalNotConfiguredError)
    def _nocfg(_: Request, exc: PayPalNotConfiguredError) -> JSONResponse:
        return JSONResponse({"code": "PAYPAL_NOT_CONFIGURED", "message": str(exc)}, status_code=503)

    @app.exception_handler(AgreementError)
    def _agreement(_: Request, exc: AgreementError) -> JSONResponse:
        return JSONResponse({"code": exc.code, "message": exc.message}, status_code=exc.http_status)

    @app.exception_handler(AuthError)
    def _auth(_: Request, exc: AuthError) -> JSONResponse:
        return JSONResponse(
            {"code": "UNAUTHENTICATED", "message": "valid session required"},
            status_code=401,
            headers={"WWW-Authenticate": "Bearer"},
        )

    @app.exception_handler(Forbidden)
    def _forbidden(_: Request, exc: Forbidden) -> JSONResponse:
        if exc.hide:
            return JSONResponse({"code": "NOT_FOUND", "message": "not found"}, status_code=404)
        return JSONResponse({"code": "FORBIDDEN", "message": str(exc)}, status_code=403)

    app.include_router(routes_system.router)
    app.include_router(routes_session.router)
    app.include_router(routes_agreements.router)
    app.include_router(routes_payments.public)
    app.include_router(routes_payments.dev)
    log.info("api started", extra={"data": settings.public_summary()})
    return app


app = create_app()
