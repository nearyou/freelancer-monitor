from __future__ import annotations


def configure_system_trust() -> None:
    """Use the operating system certificate store for all HTTPS clients."""
    try:
        import truststore
    except ImportError as exc:
        raise RuntimeError(
            "The 'truststore' dependency is required for secure HTTPS. "
            "Run: python -m pip install -e ."
        ) from exc
    truststore.inject_into_ssl()
