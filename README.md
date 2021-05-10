# rabbitmq-operator

## Description

Charmed RabbitMQ operator for Kubernetes.

## Usage

### Deploy

.. code:: bash

 juju deploy ./rabbitmq-operator.charm --resource rabbitmq-image=rabbitmq
 juju deploy nginx-ingress-integrator
 juju add-relation rabbitmq-operator nginx-ingress-integrator


### Access the RabbitMQ managment web UI

.. code:: bash
 # Let the model settle
 juju status
 # Get Ingress with service IP
 juju run-action --wait rabbitmq-operator/0 get-operator-info
 # Get user and password for administrive operator user
 
In a browser
* Connect to the Ingresss with service IP 
* Login with "operator" and the password from get-operator-info


## Developing

Create and activate a virtualenv with the development requirements:

    virtualenv -p python3 venv
    source venv/bin/activate
    pip install -r requirements-dev.txt

## Testing

The Python operator framework includes a very nice harness for testing
operator behaviour without full deployment. Just `run_tests`:

    ./run_tests
