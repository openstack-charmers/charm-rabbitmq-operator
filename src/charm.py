#!/usr/bin/env python3
# Copyright 2021 David Ames
# See LICENSE file for licensing details.
#
# Learn more at: https://juju.is/docs/sdk

"""RabbitMQ Operator Charm
"""

import logging
import pwgen
import rabbitmq_admin
import requests
import tenacity
from typing import Union

from charms.nginx_ingress_integrator.v0.ingress import IngressRequires
from charms.sunbeam_rabbitmq_operator.v0.amqp import RabbitMQAMQPProvides
from ops.charm import CharmBase
from ops.framework import StoredState
from ops.main import main
from ops.model import ActiveStatus, BlockedStatus, WaitingStatus, Relation
from ops.pebble import PathError
import interface_rabbitmq_operator_peers

logger = logging.getLogger(__name__)

RABBITMQ_CONTAINER = "rabbitmq"
RABBITMQ_SERVER_SERVICE = "rabbitmq-server"
RABBITMQ_COOKIE_PATH = "/var/lib/rabbitmq/.erlang.cookie"


class RabbitMQOperatorCharm(CharmBase):
    """Rabbit MQ Operator Charm"""

    _stored = StoredState()
    _operator_user = "operator"

    def __init__(self, *args):
        super().__init__(*args)

        self.framework.observe(
            self.on.rabbitmq_pebble_ready, self._on_rabbitmq_pebble_ready
        )
        self.framework.observe(self.on.config_changed, self._on_config_changed)
        self.framework.observe(
            self.on.get_operator_info_action, self._on_get_operator_info_action
        )
        self.framework.observe(self.on.update_status, self._on_update_status)
        # Peers
        self.peers = interface_rabbitmq_operator_peers.RabbitMQOperatorPeers(
            self, "peers"
        )
        self.framework.observe(
            self.peers.on.peers_relation_created,
            self._on_peers_relation_created,
        )
        # AMQP Provides
        self.amqp_provider = RabbitMQAMQPProvides(self, "amqp")
        self.framework.observe(
            self.amqp_provider.on.ready_amqp_clients,
            self._on_ready_amqp_clients,
        )

        self._stored.set_default(enabled_plugins=[])
        self._stored.set_default(pebble_ready=False)
        self._stored.set_default(rmq_started=False)
        self._stored.set_default(rabbitmq_version=None)

        self.ingress_mgmt = IngressRequires(
            self,
            {
                "service-hostname": "rabbitmq-management.juju",
                "service-name": self.app.name,
                "service-port": 15672,
            },
        )

        self._enable_plugin("rabbitmq_management")
        self._enable_plugin("rabbitmq_peer_discovery_k8s")

    def _on_rabbitmq_pebble_ready(self, event) -> None:
        """Define and start rabbitmq workload using the Pebble API.

        :returns: None
        :rtype: None
        """
        self._stored.pebble_ready = True

    def _on_config_changed(self, event) -> None:
        """Update configuration for RabbitMQ

        :returns: None
        :rtype: None
        """
        # Ensure rabbitmq container is up and pebble is ready
        if not self._stored.pebble_ready:
            event.defer()
            return

        # Ensure that erlang cookie is consistent across units
        if not self.unit.is_leader() and not self.peers.erlang_cookie:
            event.defer()
            return

        # Render and push configuration files
        self._render_and_push_config_files()

        # Get the rabbitmq container so we can configure/manipulate it
        container = self.unit.get_container(RABBITMQ_CONTAINER)

        # Ensure erlang cookie is consistent
        if self.peers.erlang_cookie:
            container.push(
                RABBITMQ_COOKIE_PATH,
                self.peers.erlang_cookie,
                permissions=0o600,
            )

        # Add intial Pebble config layer using the Pebble API
        container.add_layer("rabbitmq", self._rabbitmq_layer(), combine=True)

        # Autostart any services that were defined with startup: enabled
        if not container.get_service(RABBITMQ_SERVER_SERVICE).is_running():
            logging.info("Autostarting rabbitmq")
            container.autostart()
        else:
            logging.debug("RabbitMQ service is running")

        @tenacity.retry(wait=tenacity.wait_exponential(multiplier=1, min=4, max=10))
        def _check_rmq_running():
            logging.info('Waiting for RabbitMQ to start')
            if not self.rabbit_running:
                raise tenacity.TryAgain()

        _check_rmq_running()
        self._stored.rmq_started = True
        self._on_update_status(event)

    def _rabbitmq_layer(self) -> dict:
        """RabbitMQ layer definition.

        :returns: Pebble layer configuration
        :rtype: dict
        """
        return {
            "summary": "RabbitMQ layer",
            "description": "pebble config layer for RabbitMQ",
            "services": {
                RABBITMQ_SERVER_SERVICE: {
                    "override": "replace",
                    "summary": "RabbitMQ Server",
                    "command": "rabbitmq-server",
                    "startup": "enabled",
                },
            },
        }

    def _on_peers_relation_created(self, event) -> None:
        """Event handler on peers relation created.

        :returns: None
        :rtype: None
        """
        # Defer any peer relation setup until RMQ is actually running
        if not self._stored.rmq_started:
            event.defer()
            return

        logging.info("Peer relation instantiated.")

        if not self.peers.erlang_cookie and self.unit.is_leader():
            logging.debug("Providing erlang cookie to cluster")
            container = self.unit.get_container(RABBITMQ_CONTAINER)
            try:
                with container.pull(RABBITMQ_COOKIE_PATH) as cfile:
                    erlang_cookie = cfile.read().rstrip()
                self.peers.set_erlang_cookie(erlang_cookie)
            except PathError:
                logging.debug(
                    "RabbitMQ not started, deferring cookie provision"
                )
                event.defer()

        if not self.peers.operator_user_created and self.unit.is_leader():
            logging.debug(
                "Attempting to initialize from on peers relation created."
            )
            # Generate the operator user/password
            try:
                self._initialize_operator_user()
                logging.debug("Operator user initialized.")
            except requests.exceptions.HTTPError as e:
                if e.errno == 401:
                    logging.error("Authorization failed")
                    raise e
            except requests.exceptions.ConnectionError as e:
                if "Caused by NewConnectionError" in e.__str__():
                    logging.warning(
                        "RabbitMQ is not ready. Deferring. {}".format(
                            e.__str__()
                        )
                    )
                    event.defer()
                else:
                    raise e

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
        for amqp in self.framework.model.relations["amqp"]:
            # We only care about one relation. Returning the first.
            return amqp

    @property
    def amqp_bind_address(self) -> str:
        """Bind address for AMQP.

        :returns: Pebble layer configuration
        :rtype: str
        """
        return str(self.model.get_binding("amqp").network.bind_address)

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
        self, username, vhost, configure=".*", write=".*", read=".*"
    ) -> None:
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
            username, vhost, configure=configure, write=write, read=read
        )

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
        return f"{self.app.name}-endpoints.{self.model.name}.svc.cluster.local"

    @property
    def _rabbitmq_mgmt_url(self) -> str:
        """RabbitMQ Management URL

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

    @property
    def rabbit_running(self) -> bool:
        """Check whether RabbitMQ is running by checking its API

        :returns: whether service is up and running
        :rtype: bool
        """
        try:
            if self.peers.operator_user_created:
                # Use operator once created
                api = self._get_admin_api(self._operator_user, self._operator_password)
            else:
                # Fallback to guest user during early charm lifecycle
                api = self._get_admin_api("guest", "guest")
            # Cache product version from overview check for later use
            overview = api.overview()
            self._stored.rabbitmq_version = overview.get('product_version')
        except requests.exceptions.ConnectionError:
            return False
        return True

    def _get_admin_api(self, username, password) -> rabbitmq_admin.AdminAPI:
        """Return an administravie API for RabbitMQ.

        :returns: The administravie API object
        :rtype: rabbitmq_admin.AdminAPI
        """
        return rabbitmq_admin.AdminAPI(
            url=self._rabbitmq_mgmt_url, auth=(username, password)
        )

    def _initialize_operator_user(self) -> None:
        """Initialize the operator administravie user.

        By default, the RabbitMQ admin interface has an administravie user
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
        api.create_user(
            self._operator_user,
            self._operator_password,
            tags=["administrator"],
        )
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
        self._render_and_push_rabbitmq_env()

    def _render_and_push_enabled_plugins(self) -> None:
        """Render and push enabled plugins config.

        Render enabled plugins and push to the workload container.

        :returns: None
        :rtype: None
        """
        container = self.unit.get_container(RABBITMQ_CONTAINER)
        enabled_plugins = ",".join(self._stored.enabled_plugins)
        enabled_plugins_template = f"[{enabled_plugins}]."
        logger.info("Pushing new enabled_plugins")
        container.push(
            "/etc/rabbitmq/enabled_plugins", enabled_plugins_template
        )

    def _render_and_push_rabbitmq_conf(self):
        """Render and push rabbitmq conf.

        Render rabbitmq conf and push to the workload container.

        :returns: None
        :rtype: None
        """
        container = self.unit.get_container(RABBITMQ_CONTAINER)
        loopback_users = "none"
        rabbitmq_conf = f"""
# allowing remote connections for default user is highly discouraged
# as it dramatically decreases the security of the system. Delete the user
# instead and create a new one with generated secure credentials.
loopback_users = {loopback_users}

# enable k8s clustering
cluster_formation.peer_discovery_backend = k8s

# SSL access to K8S API
cluster_formation.k8s.host = kubernetes.default.svc.cluster.local
cluster_formation.k8s.port = 443
cluster_formation.k8s.scheme = https
cluster_formation.k8s.service_name = {self.app.name}

# Cluster cleanup and autoheal
cluster_formation.node_cleanup.interval = 30
cluster_formation.node_cleanup.only_log_warning = true
cluster_partition_handling = autoheal

queue_master_locator = min-masters
"""
        logger.info("Pushing new rabbitmq.conf")
        container.push("/etc/rabbitmq/rabbitmq.conf", rabbitmq_conf)

    def _render_and_push_rabbitmq_env(self):
        """Render and push rabbitmq-env conf.

        Render rabbitmq-env conf and push to the workload container.

        :returns: None
        :rtype: None
        """
        container = self.unit.get_container(RABBITMQ_CONTAINER)
        rabbitmq_env = f"""
# Sane configuration defaults for running under K8S
NODENAME=rabbit@{self.amqp_bind_address}
USE_LONGNAME=true
"""
        logger.info("Pushing new rabbitmq-env.conf")
        container.push("/etc/rabbitmq/rabbitmq-env.conf", rabbitmq_env)

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
        if not self.peers.operator_user_created:
            self.unit.status = WaitingStatus(
                "Waiting for leader to create operator user"
            )
            return

        if not self.peers.erlang_cookie:
            self.unit.status = WaitingStatus(
                "Waiting for leader to provide erlang cookie"
            )
            return

        if not self.rabbit_running:
            self.unit.status = BlockedStatus(
                "RabbitMQ not running"
            )
            return

        if self._stored.rabbitmq_version:
            self.unit.set_workload_version(self._stored.rabbitmq_version)
        self.unit.status = ActiveStatus()


if __name__ == "__main__":
    # Note: use_juju_for_storage=True required per
    # https://github.com/canonical/operator/issues/506
    main(RabbitMQOperatorCharm, use_juju_for_storage=True)
