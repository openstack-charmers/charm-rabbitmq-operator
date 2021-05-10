# Copyright 2021 David
# See LICENSE file for licensing details.
#
# Learn more about testing at: https://juju.is/docs/sdk/testing

import unittest
from unittest.mock import Mock

from charm import RabbitMQOperatorCharm
from ops.model import ActiveStatus
from ops.testing import Harness


class TestCharm(unittest.TestCase):
    def setUp(self):
        self.harness = Harness(RabbitMQOperatorCharm)
        self.addCleanup(self.harness.cleanup)
        self.harness.begin()

    def test_config_changed(self):
        self.assertEqual(list(self.harness.charm._stored.enabled_plugins), [])
        # Mock the file push
        self.harness.charm._render_and_push_config_files = Mock()
        self.harness.update_config({"enabled_plugins": "rabbitmq_management"})
        self.assertEqual(list(self.harness.charm._stored.enabled_plugins), ["rabbitmq_management"])

    def test_action(self):
        # the harness doesn't (yet!) help much with actions themselves
        action_event = Mock(params={"fail": ""})
        self.harness.charm._on_get_operator_info_action(action_event)

        self.assertTrue(action_event.set_results.called)

    def test_rabbitmq_pebble_ready(self):
        # Check the initial Pebble plan is empty
        initial_plan = self.harness.get_container_pebble_plan("rabbitmq")
        self.assertEqual(initial_plan.to_yaml(), "{}\n")
        # Expected plan after Pebble ready with default config
        expected_plan = {
            "services": {
                "rabbitmq-server": {
                    "override": "replace",
                    "summary": "RabbitMQ Server",
                    "command": "rabbitmq-server",
                    "startup": "enabled",
                },
            },
        }
        # Get the rabbitmq container from the model
        container = self.harness.model.unit.get_container("rabbitmq")
        # Emit the PebbleReadyEvent carrying the rabbitmq container
        self.harness.charm.on.rabbitmq_pebble_ready.emit(container)
        # Get the plan now we've run PebbleReady
        updated_plan = self.harness.get_container_pebble_plan("rabbitmq").to_dict()
        # Check we've got the plan we expected
        self.assertEqual(expected_plan, updated_plan)
        # Check the service was started
        service = self.harness.model.unit.get_container("rabbitmq").get_service("rabbitmq-server")
        self.assertTrue(service.is_running())
        # Ensure we set an ActiveStatus with no message
        self.assertEqual(self.harness.model.unit.status, ActiveStatus())
