from app.main import checkout


def test_checkout_regression() -> None:
    checkout(42)
