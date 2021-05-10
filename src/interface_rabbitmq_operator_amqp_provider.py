#!/usr/bin/env python3
# Copyright 2021 Canonical Ltd.
# See LICENSE file for licensing details.

"""RabbitMQAMQPProvider module"""

import logging

from ops.framework import (
    StoredState,
    EventBase,
    ObjectEvents,
    EventSource,
    Object)

logger = logging.getLogger(__name__)


class HasAMQPClientsEvent(EventBase):
    """Has AMQPClients Event."""
    pass


class ReadyAMQPClientsEvent(EventBase):
    pass


class RabbitMQAMQPClientEvents(ObjectEvents):
    has_amqp_clients = EventSource(HasAMQPClientsEvent)
    ready_amqp_clients = EventSource(ReadyAMQPClientsEvent)


class RabbitMQAMQPProvider(Object):
    """
    RabbitMQAMQPProvider class
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
        logging.debug("RabbitMQAMQPProvider on_joined")
        self.on.has_amqp_clients.emit()

    def _on_amqp_relation_changed(self, event):
        logging.debug("RabbitMQAMQPProvider on_changed")
        # Validate data on the relation
        if self.username(event) and self.vhost(event):
            self.on.ready_amqp_clients.emit()

    def _on_amqp_relation_broken(self, event):
        logging.debug("RabbitMQAMQPProvider on_departed")
        # TODO clear data on the relation

    def username(self, event):
        return event.relation.data[self.charm.app].get("username")

    def vhost(self, event):
        return event.relation.data[self.charm.app].get("vhost")

    def set_amqp_credentials(self, event, hostname, username, password):
        logging.debug("Setting amqp connection information")
        event.relation.data[self.charm.app]["hostname"] = hostname
        event.relation.data[self.charm.app]["username"] = username
        event.relation.data[self.charm.app]["password"] = password
        # Older clients expect the vhost back
        event.relation.data[self.charm.app]["vhost"] = (
            self.vhost(event))
        # TODO TLS Support. The reactive interfaces set ssl_port and ssl_ca
