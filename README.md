# rabbitmq-operator

## Description

Charmed [RabbitMQ][rabbitmq-upstream] operator for Kubernetes.

RabbitMQ is an open source multi-protocol messaging broker. The charmed
RabbitMQ operator deploys RabbitMQ as a workload on Kubernetes. It grants
access to the RabbitMQ management web interface. Having the operator charmed
allows for consuming charmed applications to simply add a relation in order to
begin using the message broker immediately.


## Usage

### Deploy

#### Simple deployment

```
 juju deploy sunbeam-rabbitmq-operator
 juju deploy nginx-ingress-integrator
 juju add-relation sunbeam-rabbitmq-operator nginx-ingress-integrator
```

#### Relate consuming client operators

```
juju add-relation <app-name>:amqp sunbeam-rabbitmq-operator:amqp
```

### Access the RabbitMQ management web UI

```
 # Let the model settle
 # Get the ingress service IP
 juju status
 # Get user and password for administrive operator user
 juju run-action --wait sunbeam-rabbitmq-operator/0 get-operator-info
```

In a browser:
* Connect to the ingress service IP and port 15672
  * Example: http://10.152.183.37:15672
* Login with the `operator` user and the password from get-operator-info

### Actions

* `get-operator-info`


<!-- LINKS -->
[rabbitmq-upstream]: https://www.rabbitmq.com/
[rabbitmq-docker-image]: https://hub.docker.com/_/rabbitmq
