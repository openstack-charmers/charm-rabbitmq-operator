#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""RabbitMQAMQPProvides and Requires module"""

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
    pass


class RabbitMQAMQPServerEvents(ObjectEvents):
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
        return self.framework.model.get_relation(self.relation_name)

    def _on_amqp_relation_joined(self, event):
        logging.debug("RabbitMQAMQPRequires on_joined")
        self.event = event
        self.on.has_amqp_servers.relation_event = event
        self.on.has_amqp_servers.emit()
        # TODO Move to charm code once the emit has this event attached
        self.request_access(event, self.charm.username, self.charm.vhost)

    def _on_amqp_relation_changed(self, event):
        logging.debug("RabbitMQAMQPRequires on_changed")
        self.event = event
        self.request_access(event, self.charm.username, self.charm.vhost)
        if self.password(event):
            self.on.ready_amqp_servers.emit()

    def _on_amqp_relation_broken(self, event):
        # TODO clear data on the relation
        logging.debug("RabbitMQAMQPRequires on_departed")

    def password(self, event):
        return event.relation.data[self._amqp_rel.app].get("password")

    def request_access(self, event, username, vhost):
        logging.debug("Requesting AMQP user and vhost")
        event.relation.data[self.charm.app]['username'] = username
        event.relation.data[self.charm.app]['vhost'] = vhost


class HasAMQPClientsEvent(EventBase):
    """Has AMQPClients Event."""
    pass


class ReadyAMQPClientsEvent(EventBase):
    pass


class RabbitMQAMQPClientEvents(ObjectEvents):
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

    @property
    def _amqp_rel(self):
        return self.framework.model.get_relation(self.relation_name)

    def _on_amqp_relation_joined(self, event):
        logging.debug("RabbitMQAMQPProvides on_joined")
        self.on.has_amqp_clients.emit()

    def _on_amqp_relation_changed(self, event):
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
        logging.debug("RabbitMQAMQPProvides on_departed")
        # TODO clear data on the relation

    def username(self, event):
        return event.relation.data[self._amqp_rel.app].get("username")

    def vhost(self, event):
        return event.relation.data[self._amqp_rel.app].get("vhost")

    def set_amqp_credentials(self, event, username, vhost):
        # TODO: Can we move this into the charm code?
        logging.debug("Setting amqp connection information")
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
        # TODO TLS Support. The reactive interfaces set ssl_port and ssl_ca
