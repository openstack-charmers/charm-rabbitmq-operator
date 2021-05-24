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
import requests
from typing import Union

from charms.nginx_ingress_integrator.v0.ingress import IngressRequires
from charms.thedac_rabbitmq_operator.v0.amqp import RabbitMQAMQPProvides
from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, WaitingStatus, Relation
import interface_rabbitmq_operator_peers

logger = logging.getLogger(__name__)

RABBITMQ_CONTAINER = "rabbitmq"
RABBITMQ_SERVER_SERVICE = "rabbitmq-server"


class RabbitMQOperatorCharm(CharmBase):
    """Rabbit MQ Operator Charm"""

    _stored = StoredState()
    _operator_user = "operator"

    def __init__(self, *args):
        super().__init__(*args)

        self.framework.observe(self.on.rabbitmq_pebble_ready, self._on_rabbitmq_pebble_ready)
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(self.on.get_operator_info_action, self._on_get_operator_info_action)
        self.framework.observe(self.on.update_status, self._on_update_status)
        # Peers
        self.peers = interface_rabbitmq_operator_peers.RabbitMQOperatorPeers(self, "peers")
        self.framework.observe(self.peers.on.has_peers, self._on_has_peers)
        # AMQP Provides
        self.amqp_provider = (RabbitMQAMQPProvides(self, "amqp"))
        self.framework.observe(
            self.amqp_provider.on.ready_amqp_clients, self._on_ready_amqp_clients)

        self._stored.set_default(users={})
        self._stored.set_default(operator={})
        self._stored.set_default(enabled_plugins=[])

        self.ingress_mgmt = IngressRequires(self, {
            "service-hostname": "rabbitmq-management.juju",
            "service-name": self.app.name,
            "service-port": 15672
        })

    def _on_rabbitmq_pebble_ready(self, event) -> None:
        """Define and start rabbitmq workload using the Pebble API.

        :returns: None
        :rtype: None
        """
        # Get a reference the container attribute on the PebbleReadyEvent
        container = event.workload
        # Add intial Pebble config layer using the Pebble API
        container.add_layer("rabbitmq", self._rabbitmq_layer(), combine=True)
        # Autostart any services that were defined with startup: enabled
        if not container.get_service(RABBITMQ_SERVER_SERVICE).is_running():
            logging.info("Autostarting rabbitmq")
            container.autostart()
        else:
            logging.debug("Rabbitmq service is running")
        self._on_update_status(event)

    def _on_config_changed(self, event) -> None:
        """Config changed.

        :returns: None
        :rtype: None
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

        if (not self.peers.operator_user_created and
                self.unit.is_leader()):
            # Generate the operator user/password
            logging.info("Attempting to initilize operator user.")
            try:
                self._initialize_operator_user()
            except requests.exceptions.HTTPError as e:
                if e.errno == 401:
                    logging.error("Athorization failed")
                    raise e
            except requests.exceptions.ConnectionError as e:
                logging.warning("Rabbitmq is not ready. Defering. Errno: {}".format(e.errno))
                event.defer()

        self._on_update_status(event)

    def _rabbitmq_layer(self) -> dict:
        """Rabbitmq layer definition.

        :returns: Pebble layer configuration
        :rtype: dict
        """
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

    def _on_has_peers(self, event) -> None:
        """Event handler on has peers.

        :returns: None
        :rtype: None
        """
        logging.info("Peer relation instantiated.")
        if (not self.peers.operator_user_created and
                self.unit.is_leader()):

            logging.info("Attempting to initilize from on has peers.")
            # Generate the operator user/password
            try:
                self._initialize_operator_user()
            except requests.exceptions.HTTPError as e:
                if e.errno == 401:
                    logging.error("Athorization failed")
                    raise e
            except requests.exceptions.ConnectionError as e:
                logging.warning(
                    "Rabbitmq is not ready. Defering. Errno: {}".format(e.response.status_code))
                event.defer()
        self._on_update_status(event)

    def _on_ready_amqp_clients(self, event) -> None:
        """Event handler on AMQP clients ready.

        :returns: None
        :rtype: None
        """
        self._on_update_status(event)

    @property
    def amqp_rel(self) -> Relation:
        """AMQP relation.

        :returns: Pebble layer configuration
        :rtype: ops.model.Relation
        """
        return self.framework.model.get_relation("amqp")

    @property
    def amqp_bind_address(self) -> str:
        """Bind address for AMQP.

        :returns: Pebble layer configuration
        :rtype: str
        """
        return str(
            self.model.get_binding(self.amqp_rel).network.bind_address)

    def does_user_exist(self, username) -> Union[str, None]:
        """Does the username exist in rabbitmq?

        Return the username if exists.
        Return None if not.

        :param username: Username to check
        :type username: str
        :returns: String username or None
        :rtype: Union[str, None]
        """
        api = self._get_admin_api(self._operator_user, self._operator_password)
        try:
            api.get_user(username)
        except requests.exceptions.HTTPError as e:
            # Username does not exist
            if e.response.status_code == 404:
                return
            else:
                raise e

    def does_vhost_exist(self, vhost) -> Union[str, None]:
        """Does the vhost exist in rabbitmq?

        Return the username if exists.
        Return None if not.

        :param username: vhost to check
        :type username: str
        :returns: String vhost or None
        :rtype: Union[str, None]
        """
        api = self._get_admin_api(self._operator_user, self._operator_password)
        try:
            api.get_vhost(vhost)
        except requests.exceptions.HTTPError as e:
            # Vhost does not exist
            if e.response.status_code == 404:
                return
            else:
                raise e

    def create_user(self, username) -> str:
        """Create user in rabbitmq.

        Return the password for the user.

        :param username: vhost to check
        :type username: str
        :returns: String password
        :rtype: str
        """
        api = self._get_admin_api(self._operator_user, self._operator_password)
        _password = pwgen.pwgen(12)
        api.create_user(username, _password)
        return _password

    def set_user_permissions(
            self, username, vhost,
            configure=".*", write=".*", read=".*") -> None:
        """Set user permissions.

        Return the password for the user.

        :param username: User to change permission on
        :type username: str
        :param configure: Configure perms. Default ".*"
        :type configure: str
        :param write: Write perms. Default ".*"
        :type write: str
        :param read: Read perms. Default ".*"
        :type read: str
        :returns: None
        :rtype: None
        """
        api = self._get_admin_api(self._operator_user, self._operator_password)
        api.create_user_permission(
            username, vhost, configure=configure, write=write, read=read)

    def create_vhost(self, vhost) -> None:
        """Create vhost in rabbitmq.

        :param vhost: Vhost to create.
        :type vhost: str
        :returns: None
        :rtype: None
        """
        api = self._get_admin_api(self._operator_user, self._operator_password)
        api.create_vhost(vhost)

    def _enable_plugin(self, plugin) -> None:
        """Enable plugin.

        Update self._stored.enabled_plugins list.

        :param plugin: Plugin to enable.
        :type plugin: str
        :returns: None
        :rtype: None
        """
        if plugin not in self._stored.enabled_plugins:
            self._stored.enabled_plugins.append(plugin)

    def _disable_plugin(self, plugin) -> None:
        """Disable plugin.

        Update self._stored.enabled_plugins list.

        :param plugin: Plugin to disable.
        :type plugin: str
        :returns: None
        :rtype: None
        """
        if plugin in self._stored.enabled_plugins:
            self._stored.enabled_plugins.remove(plugin)

    @property
    def hostname(self) -> str:
        """Hostname for access to rabbitmq.

        :returns: String IP
        :rtype: str
        """
        return self.amqp_bind_address

    @property
    def _rabbitmq_mgmt_url(self) -> str:
        """Rabbitmq Management URL

        :returns: String URL
        :rtype: str
        """
        # Use localhost for admin ACL
        return "http://localhost:15672"

    @property
    def _operator_password(self) -> Union[str, None]:
        """Return the operator password.

        If the operator password does not exist on the peer relation, create a
        new one and update the peer relation. It is necessary to store this on
        the peer relation so that it is not lost on any one unit's local
        storage. If the leader is deposed, the new leader continues to have
        administrative access to the message queue.

        :returns: String password or None
        :rtype: Unition[str, None]
        """
        # TODO: For now we are storing this in the peer relation so that all
        # peers have access to it. Move to Kubernetes secrets.
        if not self.peers.operator_password and self.unit.is_leader():
            self.peers.set_operator_password(pwgen.pwgen(12))
        return self.peers.operator_password

    def _get_admin_api(self, username, password) -> rabbitmq_admin.AdminAPI:
        """Return an administravie API for Rabbitmq.

        :returns: The administravie API object
        :rtype: rabbitmq_admin.AdminAPI
        """
        return rabbitmq_admin.AdminAPI(
            url=self._rabbitmq_mgmt_url, auth=(username, password))

    def _initialize_operator_user(self) -> None:
        """Initialize the operator administravie user.

        By default, the Rabbitmq admin interface has an administravie user
        'guest' with password 'guest'. We are exposing the admin interface so we
        must create a new administravie user and remove the guest user.

        Create the 'operator' administravie user, grant it permissions and tell
        the peer relation this is done.

        Burn the bridge behind us and remove the guest user.

        :returns: None
        :rtype: None
        """
        logging.info("Initializing the operator user.")
        # Use guest to create operator user
        api = self._get_admin_api("guest", "guest")
        api.create_user(self._operator_user, self._operator_password, tags=["administrator"])
        api.create_user_permission(self._operator_user, vhost="/")
        self.peers.set_operator_user_created(self._operator_user)
        # Burn the bridge behind us. We do not want to leave a known user/pass available
        logging.warning("Deleting the guest user.")
        api.delete_user("guest")

    def _render_and_push_config_files(self) -> None:
        """Render and push configuration files.

        Allow calling one utility function to render and push all required
        files. Calls specific render and push methods.

        :returns: None
        :rtype: None
        """
        self._render_and_push_enabled_plugins()
        self._render_and_push_rabbitmq_conf()

    def _render_and_push_enabled_plugins(self) -> None:
        """Render and push enabled plugins config.

        Render enabled plugins and push to the workload container.

        :returns: None
        :rtype: None
        """
        container = self.unit.get_container(RABBITMQ_CONTAINER)
        enabled_plugins_template = "[{enabled_plugins}]."
        ctxt = {
            "enabled_plugins": ","
            .join(self._stored.enabled_plugins)}
        logger.info("Pushing new enabled_plugins")
        container.push("/etc/rabbitmq/enabled_plugins", enabled_plugins_template.format(**ctxt))

    def _render_and_push_rabbitmq_conf(self):
        """Render and push rabbitmq conf.

        Render rabbitmq conf and push to the workload container.

        :returns: None
        :rtype: None
        """
        container = self.unit.get_container(RABBITMQ_CONTAINER)
        # TODO: Obviously, there is much more we can configure here.
        rabbitmq_conf_template = """
# allowing remote connections for default user is highly discouraged
# as it dramatically decreases the security of the system. Delete the user
# instead and create a new one with generated secure credentials.
loopback_users = {loopback_users}
"""
        ctxt = {"loopback_users": "none"}
        logger.info("Pushing new rabbitmq_conf")
        container.push("/etc/rabbitmq/rabbitmq.conf", rabbitmq_conf_template.format(**ctxt))

    def _on_get_operator_info_action(self, event) -> None:
        """Action to get operator user and password information.

        Set event results with operator user and password for accessing the
        administrative web interface.

        :returns: None
        :rtype: None
        """
        data = {
            "operator-user": self._operator_user,
            "operator-password": self._operator_password,
        }
        event.set_results(data)

    def _on_update_status(self, event) -> None:
        """Update status.

        Determine the state of the charm and set workload status.

        :returns: None
        :rtype: None
        """
        # TODO: There is much more we can do here.
        if self.peers.operator_user_created:
            if self.amqp_rel:
                if self.amqp_rel.data[self.amqp_rel.app].get("vhost"):
                    self.unit.status = ActiveStatus()
                else:
                    self.unit.status = WaitingStatus("AMQP relation incomplete")
            else:
                self.unit.status = WaitingStatus("Ready but waiting for an AMQP client relation.")
        else:
            self.unit.status = WaitingStatus("Waiting to initilaize operator user")


if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(RabbitMQOperatorCharm, use_juju_for_storage=True)
