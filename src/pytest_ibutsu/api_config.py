import os

from ibutsu_client.configuration import Configuration

CA_BUNDLE_ENVS = ["REQUESTS_CA_BUNDLE", "IBUTSU_CA_BUNDLE"]


def normalize_server_url(server_url: str) -> str:
    """Normalize server URL to ensure it ends with /api.

    Args:
        server_url: Raw server URL (may or may not include /api suffix)

    Returns:
        Normalized URL ending with /api
    """
    url = server_url.rstrip("/")
    if not url.endswith("/api"):
        url += "/api"
    return url


def create_api_configuration(
    server_url: str,
    access_token: str | None = None,
    *,
    use_ssl_ca_cert: bool = True,
) -> Configuration:
    """This handles URL normalization and common configuration setup.

    Args:
        server_url: Server URL (with or without /api suffix, with or without trailing slash)
        access_token: JWT token for authentication
        use_ssl_ca_cert: Whether to configure SSL CA cert from environment variables

    Returns:
        Configured ibutsu_client Configuration instance
    """
    normalized_url = normalize_server_url(server_url)
    config = Configuration(access_token=access_token, host=normalized_url)

    if use_ssl_ca_cert:
        for env_var in CA_BUNDLE_ENVS:
            ca_cert = os.getenv(env_var)
            if ca_cert:
                config.ssl_ca_cert = ca_cert
                break

    return config
