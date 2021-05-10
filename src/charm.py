#!/usr/bin/env python3
# Copyright 2021 David
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""Charm the service.

Refer to the following post for a quick-start guide that will help you
develop a new k8s charm using the Operator Framework:

    https://discourse.charmhub.io/t/4208
"""

import logging
import pwgen
import rabbitmq_admin
from typing import Union

from charms.nginx_ingress_integrator.v0.ingress import IngressRequires
from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus
import interface_rabbitmq_operator_peers
import interface_rabbitmq_operator_amqp_provider

logger = logging.getLogger(__name__)

RABBITMQ_CONTAINER = "rabbitmq"
RABBITMQ_SERVER_SERVICE = "rabbitmq-server"


class RabbitMQOperatorCharm(CharmBase):
    """Charm the service."""

    _stored = StoredState()
    _operator_user = "operator"

    def __init__(self, *args):
        super().__init__(*args)
        self.framework.observe(self.on.rabbitmq_pebble_ready, self._on_rabbitmq_pebble_ready)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.get_operator_info_action, self._on_get_operator_info_action)
        # Peers
        self.peers = interface_rabbitmq_operator_peers.RabbitMQOperatorPeers(self, "peers")
        self.framework.observe(self.peers.on.has_peers, self._on_has_peers)
        # AMQP Provider
        self.amqp_provider = (
            interface_rabbitmq_operator_amqp_provider.RabbitMQAMQPProvider(self, "amqp"))
        # self.framework.observe(self.amqp_provider.on.has_peers, self._on_has_amqp_clients)
        self.framework.observe(
            self.amqp_provider.on.ready_amqp_clients, self._on_ready_amqp_clients)

        self._stored.set_default(operator={})
        self._stored.set_default(enabled_plugins=[])

        self.ingress_mgmt = IngressRequires(self, {
            "service-hostname": "rabbitmq-management.juju",
            "service-name": self.app.name,
            "service-port": 15672
        })

    def _on_rabbitmq_pebble_ready(self, event):
        """Define and start a workload using the Pebble API.
        """
        # Get a reference the container attribute on the PebbleReadyEvent
        container = event.workload
        # Add intial Pebble config layer using the Pebble API
        container.add_layer("rabbitmq", self._rabbitmq_layer(), combine=True)
        # Autostart any services that were defined with startup: enabled
        if not container.get_service(RABBITMQ_SERVER_SERVICE).is_running():
            container.autostart()
        self.unit.status = ActiveStatus()

    def _on_config_changed(self, _):
        """Config changed.
        """
        management_plugin = self.config["management_plugin"]
        if management_plugin:
            logger.info("Enabling mangement_plugin {}")
            self._enable_plugin("rabbitmq_management")
        else:
            self._disable_plugin("rabbitmq_management")

        # Render and push configuration files
        self._render_and_push_config_files()

        # Get the rabbitmq container so we can configure/manipulate it
        container = self.unit.get_container(RABBITMQ_CONTAINER)
        # Create a new config layer
        layer = self._rabbitmq_layer()
        # Get the current config
        plan = container.get_plan()
        if not plan.services or (
                plan.services[RABBITMQ_SERVER_SERVICE].to_dict() !=
                layer["services"][RABBITMQ_SERVER_SERVICE]):
            # Changes were made, add the new layer
            container.add_layer("rabbitmq", layer, combine=True)
            logging.info("Added updated layer 'rabbitmq' to Pebble plan")
            # Stop the service if it is already running
            if container.get_service(RABBITMQ_SERVER_SERVICE).is_running():
                container.stop(RABBITMQ_SERVER_SERVICE)
            # Restart it and report a new status to Juju
            container.start(RABBITMQ_SERVER_SERVICE)
            logging.info("Restarted rabbitmq-service service")

        # All is well, set an ActiveStatus
        self.unit.status = ActiveStatus()

    def _rabbitmq_layer(self):
        return {
            "summary": "rabbitmq layer",
            "description": "pebble config layer for rabbitmq",
            "services": {
                RABBITMQ_SERVER_SERVICE: {
                    "override": "replace",
                    "summary": "RabbitMQ Server",
                    "command": "rabbitmq-server",
                    "startup": "enabled",
                },
            },
        }

    def _on_has_peers(self, event):
        """Event handler on has peers.
        """
        logging.info("Peer relation instantiated.")
        # Generate the operator user/password
        if (not self.peers.operator_user_created and
                self.unit.is_leader()):
            self._initialize_operator_user()

    def _on_ready_amqp_clients(self, event):
        """WIP Event handler on AMQP clients ready.
        """
        pass

    def _enable_plugin(self, plugin):
        if plugin not in self._stored.enabled_plugins:
            self._stored.enabled_plugins.append(plugin)

    def _disable_plugin(self, plugin):
        if plugin in self._stored.enabled_plugins:
            self._stored.enabled_plugins.remove(plugin)

    @property
    def _rabbitmq_mgmt_url(self):
        # For now we will use http://localhost
        # TODO get service or ingress URL for the pod
        return "http://localhost:15672"

    @property
    def _operator_password(self) -> Union[str, None]:
        if not self.peers.operator_password and self.unit.is_leader():
            self.peers.set_operator_password(pwgen.pwgen(12))
        return self.peers.operator_password

    def _initialize_operator_user(self):
        if not self._operator_password:
            logging.warning(
                "Called initialize the operator user too early. "
                "Defer.")
            return
        logging.info("Initializing the operator user.")
        # Use guest to create operator user
        api = rabbitmq_admin.AdminAPI(url=self._rabbitmq_mgmt_url, auth=("guest", "guest"))
        api.create_user(self._operator_user, self._operator_password, tags=["administrator"])
        api.create_user_permission(self._operator_user, vhost="/")
        self.peers.set_operator_user_created(self._operator_user)
        # Burn the bridge behind us. We do not want to leave a known user/pass available
        logging.warning("Deleting the guest user.")
        api.delete_user("guest")

    def _render_and_push_config_files(self):
        self._render_and_push_enabled_plugins()
        self._render_and_push_rabbitmq_conf()

    def _render_and_push_enabled_plugins(self):
        container = self.unit.get_container(RABBITMQ_CONTAINER)
        enabled_plugins_template = "[{enabled_plugins}]."
        ctxt = {
            "enabled_plugins": ","
            .join(self._stored.enabled_plugins)}
        logger.info("Pushing new enabled_plugins")
        container.push("/etc/rabbitmq/enabled_plugins", enabled_plugins_template.format(**ctxt))

    def _render_and_push_rabbitmq_conf(self):
        container = self.unit.get_container(RABBITMQ_CONTAINER)
        rabbitmq_conf_template = """
# allowing remote connections for default user is highly discouraged
# as it dramatically decreases the security of the system. Delete the user
# instead and create a new one with generated secure credentials.
loopback_users = {loopback_users}
"""
        ctxt = {"loopback_users": "none"}
        logger.info("Pushing new rabbitmq_conf")
        container.push("/etc/rabbitmq/rabbitmq.conf", rabbitmq_conf_template.format(**ctxt))

    def _on_get_operator_info_action(self, event):
        """Get operator information
        """
        data = {
            "operator-user": self._operator_user,
            "operator-password": self._operator_password,
        }
        event.set_results(data)


if __name__ == "__main__":
    main(RabbitMQOperatorCharm)
