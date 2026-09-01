"""Validate environment-specific host exposure before starting Streamlit."""

from __future__ import annotations

from ipaddress import IPv4Address, IPv4Network, ip_address
from typing import Literal


DeploymentTarget = Literal["mac", "raspberry_pi"]
MAC_LOOPBACK_ADDRESS = IPv4Address("127.0.0.1")
PRIVATE_LAN_NETWORKS = (
    IPv4Network("10.0.0.0/8"),
    IPv4Network("172.16.0.0/12"),
    IPv4Network("192.168.0.0/16"),
)


def validate_streamlit_host_bind_address(
    address: str, *, deployment_target: DeploymentTarget
) -> str:
    """Allow Mac loopback or one explicit Raspberry Pi private-LAN IPv4 only."""

    try:
        parsed = ip_address(str(address).strip())
    except ValueError as error:
        raise ValueError("STREAMLIT_HOST_BIND_ADDRESS must be an IPv4 address") from error
    if not isinstance(parsed, IPv4Address):
        raise ValueError("STREAMLIT_HOST_BIND_ADDRESS must be an IPv4 address")
    if parsed.is_unspecified or parsed.is_multicast or parsed.is_link_local:
        raise ValueError("STREAMLIT_HOST_BIND_ADDRESS cannot expose all or unstable interfaces")
    if deployment_target == "mac":
        if parsed != MAC_LOOPBACK_ADDRESS:
            raise ValueError("Mac Streamlit must bind to 127.0.0.1")
    elif deployment_target == "raspberry_pi":
        if not any(parsed in network for network in PRIVATE_LAN_NETWORKS):
            raise ValueError(
                "Raspberry Pi Streamlit requires an explicit RFC1918 private LAN address"
            )
    else:
        raise ValueError("DEPLOYMENT_TARGET must be mac or raspberry_pi")
    return str(parsed)
