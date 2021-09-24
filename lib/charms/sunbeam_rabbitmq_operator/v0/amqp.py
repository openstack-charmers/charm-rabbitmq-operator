"""RabbitMQAMQPProvides and Requires module.


This library contains the Requires and Provides classes for handling
the amqp interface.

Import `RabbitMQAMQPRequires` in your charm, with the charm object and the
relation name:
    - self
    - "amqp"

Create two properties on your Charm:
    - username
    - vhost

Two events are also available to respond to:
    - has_amqp_servers
    - ready_amqp_servers

A basic example showing the usage of this relation follows:

```
from charms.sunbeam_rabbitmq_operator.v0.amqp import RabbitMQAMQPRequires

class AMQPClientCharm(CharmBase):
    def __init__(self, *args):
        super().__init__(*args)
        # AMQP Requires
        self.amqp_requires = (RabbitMQAMQPRequires(self, "amqp"))
        self.framework.observe(
            self.amqp_requires.on.has_amqp_servers, self._on_has_amqp_servers)
        self.framework.observe(
            self.amqp_requires.on.ready_amqp_servers, self._on_ready_amqp_servers)

    @property
    def vhost(self):
        '''Set the vhost for the AMQP relation.

        This could be a config option or hard-coded.
        '''
        return "amqp-client-vhost"

    @property
    def username(self):
        '''Set the username for the AMQP relation

        This could be a config option or hard-coded.
        '''
        return "amqp-client"

    def _on_has_amqp_servers(self, event):
        '''React to the AMQP relation joined.

        The AQMP interface will use self.username and self.vhost to commuicate
        with the.
        '''
        # Do something before the relation is complete

    def _on_ready_amqp_servers(self, event):
        '''React to the AMQP relation joined.

        The AQMP interface will use self.username and self.vhost for the
        request to the rabbitmq server.
        '''
        # AMQP Relation is ready. Do something with the completed relation.

    ### Interacting with the complete interface ###
    @property
    def amqp_rel(self):
        return self.framework.model.get_relation("amqp")

    @property
    def password(self):
        '''Return the password from the AMQP relation'''
        return self.amqp_rel.data[self.amqp_rel.app].get("password")

    @property
    def hostname(self):
        '''Return the hostname from the AMQP relation'''
        return self.amqp_rel.data[self.amqp_rel.app].get("hostname")
```
"""

# The unique Charmhub library identifier, never change it
LIBID = "a33664aaa3cc4b9da0ea716f08fc2cb0"

# Increment this major API version when introducing breaking changes
LIBAPI = 0

# Increment this PATCH version before using `charmcraft publish-lib` or reset
# to 0 if you are raising the major API version
LIBPATCH = 1

import logging
import requests

from ops.framework import (
    StoredState,
    EventBase,
    ObjectEvents,
    EventSource,
    Object)

logger = logging.getLogger(__name__)


class HasAMQPServersEvent(EventBase):
    """Has AMQPServers Event."""
    pass


class ReadyAMQPServersEvent(EventBase):
    """Ready AMQPServers Event."""
    pass


class RabbitMQAMQPServerEvents(ObjectEvents):
    """Events class for `on`"""
    has_amqp_servers = EventSource(HasAMQPServersEvent)
    ready_amqp_servers = EventSource(ReadyAMQPServersEvent)


class RabbitMQAMQPRequires(Object):
    """
    RabbitMQAMQPRequires class
    """

    on = RabbitMQAMQPServerEvents()
    _stored = StoredState()

    def __init__(self, charm, relation_name):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_joined, self._on_amqp_relation_joined
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_changed, self._on_amqp_relation_changed
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken, self._on_amqp_relation_broken
        )

    @property
    def _amqp_rel(self):
        """The AMQP relation."""
        return self.framework.model.get_relation(self.relation_name)

    def _on_amqp_relation_joined(self, event):
        """AMQP relation joined."""
        logging.debug("RabbitMQAMQPRequires on_joined")
        self.event = event
        self.on.has_amqp_servers.relation_event = event
        self.on.has_amqp_servers.emit()
        # TODO Move to charm code once the emit has this event attached
        self.request_access(event, self.charm.username, self.charm.vhost)

    def _on_amqp_relation_changed(self, event):
        """AMQP relation changed."""
        logging.debug("RabbitMQAMQPRequires on_changed")
        self.event = event
        self.request_access(event, self.charm.username, self.charm.vhost)
        if self.password(event):
            self.on.ready_amqp_servers.emit()

    def _on_amqp_relation_broken(self, event):
        """AMQP relation broken."""
        # TODO clear data on the relation
        logging.debug("RabbitMQAMQPRequires on_departed")

    def password(self, event):
        """Return the AMQP password from the server side of the relation."""
        return event.relation.data[event.relation.app].get("password")

    def request_access(self, event, username, vhost):
        """Request access to the AMQP server.

        :param event: The current event
        :type EventsBase
        :param username: The requested username
        :type username: str
        :param vhost: The requested vhost
        :type vhost: str
        :returns: None
        :rtype: None
        """
        if self.model.unit.is_leader():
            logging.debug("Requesting AMQP user and vhost")
            event.relation.data[self.charm.app]['username'] = username
            event.relation.data[self.charm.app]['vhost'] = vhost


class HasAMQPClientsEvent(EventBase):
    """Has AMQPClients Event."""
    pass


class ReadyAMQPClientsEvent(EventBase):
    """AMQPClients Ready Event."""
    pass


class RabbitMQAMQPClientEvents(ObjectEvents):
    """Events class for `on`"""
    has_amqp_clients = EventSource(HasAMQPClientsEvent)
    ready_amqp_clients = EventSource(ReadyAMQPClientsEvent)


class RabbitMQAMQPProvides(Object):
    """
    RabbitMQAMQPProvides class
    """

    on = RabbitMQAMQPClientEvents()
    _stored = StoredState()

    def __init__(self, charm, relation_name):
        super().__init__(charm, relation_name)
        self.charm = charm
        self.relation_name = relation_name
        self.framework.observe(
            self.charm.on[relation_name].relation_joined, self._on_amqp_relation_joined
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_changed, self._on_amqp_relation_changed
        )
        self.framework.observe(
            self.charm.on[relation_name].relation_broken, self._on_amqp_relation_broken
        )

    def _on_amqp_relation_joined(self, event):
        """Handle AMQP joined."""
        logging.debug("RabbitMQAMQPProvides on_joined")
        self.on.has_amqp_clients.emit()

    def _on_amqp_relation_changed(self, event):
        """Handle AMQP changed."""
        logging.debug("RabbitMQAMQPProvides on_changed")
        # Validate data on the relation
        if self.username(event) and self.vhost(event):
            self.on.ready_amqp_clients.emit()
            if self.charm.unit.is_leader():
                self.set_amqp_credentials(
                    event,
                    self.username(event),
                    self.vhost(event))

    def _on_amqp_relation_broken(self, event):
        """Handle AMQP broken."""
        logging.debug("RabbitMQAMQPProvides on_departed")
        # TODO clear data on the relation

    def username(self, event):
        """Return the AMQP username from the client side of the relation."""
        return event.relation.data[event.relation.app].get("username")

    def vhost(self, event):
        """Return the AMQP vhost from the client side of the relation."""
        return event.relation.data[event.relation.app].get("vhost")

    def set_amqp_credentials(self, event, username, vhost):
        """Set AMQP Credentials.

        :param event: The current event
        :type EventsBase
        :param username: The requested username
        :type username: str
        :param vhost: The requested vhost
        :type vhost: str
        :returns: None
        :rtype: None
        """
        # TODO: Can we move this into the charm code?
        # TODO TLS Support. Existing interfaces set ssl_port and ssl_ca
        logging.debug("Setting amqp connection information.")
        try:
            if not self.charm.does_vhost_exist(vhost):
                self.charm.create_vhost(vhost)
            password = self.charm.create_user(username)
            self.charm.set_user_permissions(username, vhost)
            event.relation.data[self.charm.app]["password"] = password
            event.relation.data[self.charm.app]["hostname"] = self.charm.hostname
        except requests.exceptions.ConnectionError as e:
            logging.warning("Rabbitmq is not ready. Defering. Errno: {}".format(e.errno))
            event.defer()
