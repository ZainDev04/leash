"""PayPal calls, through the official PayPal Server SDK. Leash is fixed to the sandbox, so no real money can move."""
import os
import uuid
from functools import lru_cache

from paypalserversdk.configuration import Environment
from paypalserversdk.http.auth.o_auth_2 import ClientCredentialsAuthCredentials
from paypalserversdk.models.amount_with_breakdown import AmountWithBreakdown
from paypalserversdk.models.capture_request import CaptureRequest
from paypalserversdk.models.checkout_payment_intent import CheckoutPaymentIntent
from paypalserversdk.models.order_request import OrderRequest
from paypalserversdk.models.payment_source import PaymentSource
from paypalserversdk.models.paypal_experience_user_action import PaypalExperienceUserAction
from paypalserversdk.models.paypal_payment_token_customer_type import PaypalPaymentTokenCustomerType
from paypalserversdk.models.paypal_payment_token_usage_type import PaypalPaymentTokenUsageType
from paypalserversdk.models.paypal_wallet import PaypalWallet
from paypalserversdk.models.paypal_wallet_attributes import PaypalWalletAttributes
from paypalserversdk.models.paypal_wallet_experience_context import PaypalWalletExperienceContext
from paypalserversdk.models.paypal_wallet_vault_instruction import PaypalWalletVaultInstruction
from paypalserversdk.models.purchase_unit_request import PurchaseUnitRequest
from paypalserversdk.models.store_in_vault_instruction import StoreInVaultInstruction
from paypalserversdk.paypal_serversdk_client import PaypalServersdkClient


class PayPalError(Exception):
    pass


@lru_cache
def _client():
    return PaypalServersdkClient(
        client_credentials_auth_credentials=ClientCredentialsAuthCredentials(
            o_auth_client_id=os.environ["PAYPAL_CLIENT_ID"],
            o_auth_client_secret=os.environ["PAYPAL_CLIENT_SECRET"],
        ),
        environment=Environment.SANDBOX,
        timeout=30,
        # POST is safe to retry here because every change carries a request ID, so PayPal never does it twice
        max_retries=2,
        retry_statuses=[408, 429, 500, 502, 503, 504],
        retry_methods=["GET", "POST"],
    )


def _once():
    """Headers shared by every change. The request ID makes PayPal treat a repeat as the same request."""
    return {"paypal_request_id": str(uuid.uuid4()), "prefer": "return=representation"}


def _order(payment_source, amount, currency, description):
    return _client().orders.create_order({**_once(), "body": OrderRequest(
        intent=CheckoutPaymentIntent.AUTHORIZE,
        purchase_units=[PurchaseUnitRequest(
            amount=AmountWithBreakdown(currency_code=currency, value=f"{amount:.2f}"),
            description=description[:127],
        )],
        payment_source=PaymentSource(paypal=payment_source),
    )}).body


def _authorize(order_id):
    return _client().orders.authorize_order({**_once(), "id": order_id}).body


def _authorization(order):
    return order.purchase_units[0].payments.authorizations[0]


def start_vault(return_url, cancel_url):
    """$1 order that saves the buyer's PayPal. The $1 is voided after approval."""
    order = _order(PaypalWallet(
        attributes=PaypalWalletAttributes(vault=PaypalWalletVaultInstruction(
            store_in_vault=StoreInVaultInstruction.ON_SUCCESS,
            usage_type=PaypalPaymentTokenUsageType.MERCHANT,
            customer_type=PaypalPaymentTokenCustomerType.CONSUMER,
        )),
        experience_context=PaypalWalletExperienceContext(
            return_url=return_url, cancel_url=cancel_url, user_action=PaypalExperienceUserAction.CONTINUE,
        ),
    ), 1, "USD", "Leash: save PayPal for your agents")
    return order.id, next(link.href for link in order.links if link.rel == "payer-action")


def finish_vault(order_id):
    order = _authorize(order_id)
    void(_authorization(order).id)
    vault = order.payment_source.paypal.attributes.vault
    if vault.status != "VAULTED":
        raise PayPalError(f"Vault not saved: {vault.status}")
    return vault.id


def hold(vault_id, amount, currency, description):
    """Order from the saved PayPal. It comes back already authorized, so the money is on hold."""
    order = _order(PaypalWallet(vault_id=vault_id), amount, currency, description)
    if order.status != "COMPLETED":
        order = _authorize(order.id)
    auth = _authorization(order)
    if auth.status != "CREATED":
        raise PayPalError(f"Hold failed: {auth.status}")
    return order.id, auth.id


def capture(auth_id):
    return _client().payments.capture_authorized_payment(
        {**_once(), "authorization_id": auth_id, "body": CaptureRequest()}).body.id


def void(auth_id):
    _client().payments.void_payment({**_once(), "authorization_id": auth_id})
