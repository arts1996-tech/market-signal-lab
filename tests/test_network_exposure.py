from pathlib import Path

import pytest

from app.core.network import validate_streamlit_host_bind_address


def test_streamlit_host_ports_default_to_mac_loopback():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")
    env_example = Path(".env.example").read_text(encoding="utf-8")

    expected = "${STREAMLIT_HOST_BIND_ADDRESS:-127.0.0.1}"
    assert f'\"{expected}:8501:8501\"' in compose
    assert f'\"{expected}:8502:8502\"' in compose
    assert '\"8501:8501\"' not in compose
    assert '\"8502:8502\"' not in compose
    assert "STREAMLIT_HOST_BIND_ADDRESS=127.0.0.1" in env_example


def test_container_listener_is_separate_from_host_publication():
    compose = Path("docker-compose.yml").read_text(encoding="utf-8")

    assert "python jobs/run_streamlit.py --app lab --port 8501" in compose
    assert "python jobs/run_streamlit.py --app lite --port 8502" in compose
    assert compose.count("${STREAMLIT_HOST_BIND_ADDRESS:-127.0.0.1}") == 4
    assert '\"127.0.0.1:5432:5432\"' in compose


def test_raspberry_pi_operations_require_current_lan_ip_not_all_interfaces():
    operations = Path("docs/08_raspberry_pi_operations.md").read_text(encoding="utf-8")

    assert "現在のLAN IP" in operations
    assert "STREAMLIT_HOST_BIND_ADDRESS=0.0.0.0" in operations
    assert "使用しない" in operations
    assert "DHCP" in operations


def test_mac_accepts_only_loopback_ipv4():
    assert (
        validate_streamlit_host_bind_address("127.0.0.1", deployment_target="mac")
        == "127.0.0.1"
    )
    for address in ("127.0.0.2", "192.168.1.20"):
        with pytest.raises(ValueError, match="127.0.0.1"):
            validate_streamlit_host_bind_address(address, deployment_target="mac")


def test_raspberry_pi_accepts_only_explicit_private_lan_ipv4():
    assert (
        validate_streamlit_host_bind_address(
            "192.168.100.127", deployment_target="raspberry_pi"
        )
        == "192.168.100.127"
    )
    for address in (
        "0.0.0.0",
        "127.0.0.1",
        "8.8.8.8",
        "100.64.0.1",
        "192.0.0.1",
        "raspberrypi.local",
        "::1",
    ):
        with pytest.raises(ValueError):
            validate_streamlit_host_bind_address(
                address, deployment_target="raspberry_pi"
            )


def test_streamlit_runner_uses_validated_address_for_display():
    runner = Path("jobs/run_streamlit.py").read_text(encoding="utf-8")

    assert '"--server.address=0.0.0.0"' in runner
    assert 'f"--browser.serverAddress={host_bind_address}"' in runner
