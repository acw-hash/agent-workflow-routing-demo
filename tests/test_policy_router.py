from app.config import Settings
from app.policy_router import PolicyRouter


def test_routes_fraud_queries() -> None:
    settings = Settings()
    router = PolicyRouter(settings)

    decision = router.route("I see an unauthorized transaction and suspect fraud")

    assert decision.domain == "fraud"


def test_routes_refund_queries() -> None:
    settings = Settings()
    router = PolicyRouter(settings)

    decision = router.route("What is my refund status for a merchant dispute?")

    assert decision.domain == "refunds_disputes"


def test_routes_card_service_queries() -> None:
    settings = Settings()
    router = PolicyRouter(settings)

    decision = router.route("My card was declined at the ATM")

    assert decision.domain == "card_services"


def test_routes_unknown_query() -> None:
    settings = Settings()
    router = PolicyRouter(settings)

    decision = router.route("Can you explain mortgage refinancing options?")

    assert decision.domain == "unknown"
