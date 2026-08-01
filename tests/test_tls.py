import sys
from types import SimpleNamespace
from unittest import TestCase, mock

from freelancer_monitor.tls import configure_system_trust


class TlsConfigurationTests(TestCase):
    def test_injects_operating_system_trust_store(self):
        injected = mock.Mock()
        fake_module = SimpleNamespace(inject_into_ssl=injected)
        with mock.patch.dict(sys.modules, {"truststore": fake_module}):
            configure_system_trust()
        injected.assert_called_once_with()
