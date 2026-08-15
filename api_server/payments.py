"""Payments, credit top-up, and pricing API endpoints."""

import json
import logging
import os
from typing import Optional

from fastapi import Request, HTTPException
from pydantic import BaseModel
import stripe

from api_server.shared import app, db, get_current_user, FLAGS
from utils.auth_cache import auth_session_cache
from pricing.pricing_controller import PricingController


logger = logging.getLogger(__name__)


class BuyCreditsRequest(BaseModel):
    package_id: Optional[str] = None
    custom_credits: Optional[float] = None
    custom_usd: Optional[float] = None
    payment_method: Optional[str] = None
    card_number: Optional[str] = None
    card_exp: Optional[str] = None
    card_cvc: Optional[str] = None
    card_name: Optional[str] = None
    checkout_mode: Optional[bool] = False


# ========================================
# Payments & Credit Top-Up API Endpoints
# ========================================

CREDIT_PACKAGES = {
    "starter": {
        "credits": 100.0, "amount_usd": 5.00, "name": "Starter Pack",
        "price_id": "price_1TzbWlRjBSgVFVM6b6ByPtVn",
    },
    "pro": {
        "credits": 400.0, "amount_usd": 18.00, "name": "Pro Pack",
        "price_id": "price_1TzbisRjBSgVFVM6Jmyk0IcL",
    },
    "ultra": {
        "credits": 1000.0, "amount_usd": 40.00, "name": "Ultra Pack",
        "price_id": "price_1TzbjORjBSgVFVM6cWJZy8xb",
    },
}

def _is_mock_payment_mode(payment_method: Optional[str] = None) -> bool:
    """Allow simulated payment only when an explicit test flag enables it."""
    if getattr(FLAGS, "allow_mock_payments", False):
        return True
    if getattr(FLAGS, "testing_use_local_database", False):
        return True
    return False


@app.post("/api/payments/buy-credits")
def buy_credits(req: BuyCreditsRequest, request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to purchase credits.")

    if req.package_id and req.package_id in CREDIT_PACKAGES:
        pkg = CREDIT_PACKAGES[req.package_id]
        credits_to_add = pkg["credits"]
        usd_amount = pkg["amount_usd"]
    elif req.custom_credits and req.custom_credits > 0 and req.custom_usd and req.custom_usd > 0:
        credits_to_add = req.custom_credits
        usd_amount = req.custom_usd
    elif req.package_id is None and req.custom_credits is None:
        pkg = CREDIT_PACKAGES["starter"]
        credits_to_add = pkg["credits"]
        usd_amount = pkg["amount_usd"]
    else:
        raise HTTPException(status_code=400, detail="Invalid package or credit amount specified.")

    # Older clients submit card fields without naming a payment method.  Keep
    # those requests on the card-validation path; package-only requests use
    # hosted Stripe Checkout by default.
    payment_method = req.payment_method or (
        "card_mock" if req.card_number else "stripe_checkout"
    )

    # Automated Mock Flow (for unit tests, mock flags, or unconfigured gateway)
    if _is_mock_payment_mode(payment_method):
        if payment_method.startswith("card"):
            card_num = (req.card_number or "").replace(" ", "").replace("-", "")
            if not card_num:
                raise HTTPException(status_code=400, detail="Credit card number is required.")
            
            if card_num.endswith("0002") or card_num.endswith("0000"):
                raise HTTPException(status_code=400, detail="Your card was declined by the issuer.")
            if card_num.endswith("0051"):
                raise HTTPException(status_code=400, detail="Insufficient funds on credit card.")
            
            if not card_num.isdigit() or len(card_num) < 13 or len(card_num) > 19:
                raise HTTPException(status_code=400, detail="Invalid credit card number format.")
            
            if req.card_exp:
                exp_clean = req.card_exp.replace(" ", "")
                if "/" not in exp_clean or len(exp_clean) != 5:
                    raise HTTPException(status_code=400, detail="Invalid card expiration format (must be MM/YY).")
            else:
                raise HTTPException(status_code=400, detail="Card expiration date (MM/YY) is required.")

            cvc_clean = (req.card_cvc or "").strip()
            if not cvc_clean or not cvc_clean.isdigit() or len(cvc_clean) < 3 or len(cvc_clean) > 4:
                raise HTTPException(status_code=400, detail="Invalid CVV / CVC code (must be 3 or 4 digits).")

            stripe_key = os.getenv("STRIPE_SECRET_KEY")
            allow_mock = bool(FLAGS.allow_mock_payments) or bool(FLAGS.testing_use_local_database)

            if not stripe_key and not allow_mock:
                raise HTTPException(status_code=503, detail="Payment service unavailable")

        result = db.add_user_credits(user["id"], credits_to_add, usd_amount, payment_method)
        auth_session_cache.invalidate_user(user["id"])
        return {"status": "ok", "message": f"Successfully added {credits_to_add:.1f} credits!", **result}

    # ========================================
    # Live Real Stripe Gateway Flow (Default)
    # ========================================
    stripe_key = os.getenv("STRIPE_SECRET_KEY")
    if not stripe_key:
        raise HTTPException(status_code=503, detail="Payment service unavailable")

    if not stripe:
        raise HTTPException(status_code=503, detail="Stripe library is not installed on the server.")

    stripe.api_key = stripe_key
    base_url = str(request.base_url).rstrip("/")

    # Stripe Checkout Session (Redirect flow)
    if req.checkout_mode or payment_method in ("stripe_checkout", "stripe"):
        try:
            line_item = (
                {"price": pkg["price_id"], "quantity": 1}
                if req.package_id in CREDIT_PACKAGES
                else {
                    "price_data": {
                        "currency": "usd",
                        "product_data": {"name": f"Narratron Credits - {credits_to_add:.0f} Cr"},
                        "unit_amount": int(round(usd_amount * 100)),
                    },
                    "quantity": 1,
                }
            )
            session = stripe.checkout.Session.create(
                line_items=[line_item],
                mode="payment",
                metadata={
                    "user_id": str(user["id"]),
                    "credits_to_add": str(credits_to_add),
                    "usd_amount": str(usd_amount),
                    "package_id": req.package_id or "custom",
                },
                success_url=f"{base_url}/deploy?payment=success&session_id={{CHECKOUT_SESSION_ID}}",
                cancel_url=f"{base_url}/deploy?payment=cancelled",
            )
            return {
                "status": "ok",
                "mode": "stripe_checkout",
                "checkout_url": session.url,
                "session_id": session.id,
                "credits_added": credits_to_add,
                "message": "Stripe Checkout session created successfully."
            }
        except Exception as err:
            logger.error(f"Stripe Checkout Error: {err}")
            raise HTTPException(status_code=400, detail=f"Stripe Checkout Error: {err}")

    # Direct card payment processing via Stripe PaymentIntent API
    card_num = (req.card_number or "").replace(" ", "").replace("-", "")
    if not card_num:
        raise HTTPException(status_code=400, detail="Credit card number is required.")

    try:
        exp_parts = req.card_exp.split("/") if req.card_exp and "/" in req.card_exp else ["12", "30"]
        exp_month = int(exp_parts[0])
        exp_year = int("20" + exp_parts[1] if len(exp_parts[1]) == 2 else exp_parts[1])

        pm = stripe.PaymentMethod.create(
            type="card",
            card={
                "number": card_num,
                "exp_month": exp_month,
                "exp_year": exp_year,
                "cvc": (req.card_cvc or "").strip(),
            },
            billing_details={"name": req.card_name or user.get("username", "Customer")},
        )
        intent = stripe.PaymentIntent.create(
            amount=int(round(usd_amount * 100)),
            currency="usd",
            payment_method=pm.id,
            confirm=True,
            automatic_payment_methods={"enabled": True, "allow_redirects": "never"},
            metadata={"user_id": str(user["id"]), "credits_to_add": str(credits_to_add)},
        )
        if intent.status == "succeeded":
            result = db.add_user_credits(user["id"], credits_to_add, usd_amount, "stripe_live")
            auth_session_cache.invalidate_user(user["id"])
            return {"status": "ok", "message": f"Successfully added {credits_to_add:.1f} credits!", **result}
        else:
            raise HTTPException(status_code=400, detail=f"Stripe payment status: {intent.status}")
    except Exception as e:
        logger.error(f"Stripe Direct Charge Error: {e}")
        raise HTTPException(status_code=400, detail=f"Stripe Payment Failed: {e}")


@app.get("/api/payments/verify-session")
def verify_stripe_session(session_id: str, request: Request):
    """Verify completed Stripe Checkout session and credit user account."""
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required.")

    if _is_mock_payment_mode():
        return {"status": "ok", "verified": True, "credits_added": 0.0, "user": user}

    if not stripe:
        raise HTTPException(status_code=500, detail="Stripe library not installed.")

    stripe_key = os.getenv("STRIPE_SECRET_KEY")
    if not stripe_key:
        raise HTTPException(status_code=503, detail="Stripe key missing.")

    stripe.api_key = stripe_key
    try:
        session = stripe.checkout.Session.retrieve(session_id)
        if session.payment_status == "paid":
            meta = session.metadata or {}
            meta_user_id = int(meta.get("user_id", 0))
            credits_to_add = float(meta.get("credits_to_add", 0.0))
            usd_amount = float(meta.get("usd_amount", 0.0))

            if meta_user_id == user["id"] and credits_to_add > 0:
                result = db.add_stripe_session_credits(
                    user["id"], credits_to_add, usd_amount, session_id, "stripe_checkout"
                )
                auth_session_cache.invalidate_user(user["id"])
                return {"status": "ok", "verified": True, "credits_added": credits_to_add, **result}
            return {"status": "ok", "verified": True, "user": user}
        return {"status": "pending", "verified": False, "detail": "Payment not completed."}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Failed to verify Stripe session: {e}")


@app.post("/api/payments/webhook")
async def stripe_webhook(request: Request):
    """Stripe Webhook listener for asynchronous payment settlement."""
    payload = await request.body()
    sig_header = request.headers.get("stripe-signature")
    webhook_secret = os.getenv("STRIPE_WEBHOOK_SECRET")

    event = None
    if _is_mock_payment_mode():
        try:
            event = json.loads(payload.decode("utf-8"))
        except Exception:
            raise HTTPException(status_code=400, detail="Invalid webhook JSON payload.")
    else:
        if not webhook_secret or not sig_header:
            raise HTTPException(status_code=400, detail="Stripe webhook signature is required.")
        try:
            event = stripe.Webhook.construct_event(payload, sig_header, webhook_secret)
        except Exception as e:
            raise HTTPException(status_code=400, detail=f"Webhook signature error: {e}")

    event_type = event.get("type") if isinstance(event, dict) else getattr(event, "type", None)
    data_obj = event.get("data", {}).get("object", {}) if isinstance(event, dict) else getattr(getattr(event, "data", None), "object", {})

    if event_type == "checkout.session.completed":
        metadata = data_obj.get("metadata", {})
        user_id = metadata.get("user_id")
        credits_to_add = metadata.get("credits_to_add")
        usd_amount = metadata.get("usd_amount")

        session_id = data_obj.get("id")
        if user_id and credits_to_add and session_id:
            db.add_stripe_session_credits(
                int(user_id),
                float(credits_to_add),
                float(usd_amount or 0.0),
                session_id,
                "stripe_webhook",
            )
            auth_session_cache.invalidate_user(int(user_id))

    return {"status": "ok"}

@app.get("/api/payments/history")
def get_payment_history(request: Request):
    user = get_current_user(request)
    if not user:
        raise HTTPException(status_code=401, detail="Authentication required to view payment history.")
    transactions = db.get_user_transactions(user["id"])
    return {"status": "ok", "transactions": transactions}

@app.get("/api/pricing")
def get_pricing_rates(
    voice_minutes: Optional[float] = None,
    images_created: Optional[int] = None,
    music_created: Optional[int] = None,
    story_plans: Optional[int] = None,
    adventure_actions: Optional[int] = None,
    adventure_minutes: Optional[float] = None,
    gb_amount: Optional[float] = None,
    days: Optional[float] = 1.0,
    usd_amount: Optional[float] = None,
):
    """Retrieve current pricing rates and optionally calculate costs dynamically from PricingController."""
    if voice_minutes is not None and voice_minutes < 0:
        raise HTTPException(status_code=400, detail="voice_minutes must be non-negative.")
    if images_created is not None and images_created < 0:
        raise HTTPException(status_code=400, detail="images_created must be non-negative.")
    if music_created is not None and music_created < 0:
        raise HTTPException(status_code=400, detail="music_created must be non-negative.")
    if story_plans is not None and story_plans < 0:
        raise HTTPException(status_code=400, detail="story_plans must be non-negative.")
    if adventure_actions is not None and adventure_actions < 0:
        raise HTTPException(status_code=400, detail="adventure_actions must be non-negative.")
    if adventure_minutes is not None and adventure_minutes < 0:
        raise HTTPException(status_code=400, detail="adventure_minutes must be non-negative.")
    if gb_amount is not None and gb_amount < 0:
        raise HTTPException(status_code=400, detail="gb_amount must be non-negative.")
    if days is not None and days < 0:
        raise HTTPException(status_code=400, detail="days must be non-negative.")
    if usd_amount is not None and usd_amount < 0:
        raise HTTPException(status_code=400, detail="usd_amount must be non-negative.")

    pricing = getattr(db, "pricing_controller", None) or PricingController.from_env()
    rates = pricing.get_rates()

    calculation = {}
    if (
        voice_minutes is not None
        or images_created is not None
        or music_created is not None
        or story_plans is not None
        or adventure_actions is not None
    ):
        vm = voice_minutes if voice_minutes is not None else 0.0
        ic = images_created if images_created is not None else 0
        mc = music_created if music_created is not None else 0
        sp = story_plans if story_plans is not None else 0
        aa = adventure_actions if adventure_actions is not None else 0
        calculation["usage_credits"] = pricing.calculate_usage_cost(
            voice_minutes=vm, images_created=ic, music_created=mc, story_plans=sp, adventure_actions=aa
        )

    if adventure_actions is not None or adventure_minutes is not None:
        aa = adventure_actions if adventure_actions is not None else 0
        am = adventure_minutes if adventure_minutes is not None else 0.0
        calculation["adventure_mode_credits"] = pricing.calculate_adventure_mode_cost(actions=aa, duration_minutes=am)
        calculation["adventure_mode_estimated_tokens"] = pricing.estimate_adventure_mode_tokens(actions=aa, duration_minutes=am)

    if gb_amount is not None:
        d = days if days is not None else 1.0
        calculation["storage_credits"] = pricing.calculate_storage_cost(gb_amount=gb_amount, days=d)

    if usd_amount is not None:
        calculation["usd_credits"] = pricing.credits_for_usd(usd_amount)

    if calculation:
        rates["calculation"] = calculation

    return rates
