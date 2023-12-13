MACHINE_NAME = superset-vm
REPO_FOLDER = superset-k8s-operator
CHARM_NAME = superset-k8s
INGRESS_DNS = subjectAltName=DNS:superset-k8s
REPO = https://github.com/canonical/$(REPO_FOLDER).git

.PHONY: dev build install clean

dev: install-multipass setup-multipass

build: create-model deploy-postgresql deploy-redis setup-ingress deploy-ingress deploy-superset

install: relate-ui relate-worker relate-beat

clean: remove-charms remove-model remove-vm

install-multipass:
	sudo snap install multipass

setup-multipass:
	multipass launch -n $(MACHINE_NAME) -m 8G -c 4 -d 30G --name $(MACHINE_NAME) charm-dev

create-model:
	multipass exec $(MACHINE_NAME) -- juju bootstrap microk8s superset-controller
	multipass exec $(MACHINE_NAME) -- juju add-model $(CHARM_NAME)
	multipass exec $(MACHINE_NAME) -- juju model-config logging-config="<root>=INFO;unit=DEBUG"
	multipass exec $(MACHINE_NAME) -- juju model-config update-status-hook-interval=1m
	multipass exec $(MACHINE_NAME) -- juju status

deploy-postgresql:
	multipass exec $(MACHINE_NAME) -- juju deploy postgresql-k8s --channel=14/stable --trust
	multipass exec $(MACHINE_NAME) -- juju wait-for application postgresql-k8s --query='status=="active"' --timeout 10m

deploy-redis:
	multipass exec $(MACHINE_NAME) -- juju deploy redis-k8s --channel=edge

deploy-superset:
	multipass exec $(MACHINE_NAME) -- juju deploy $(CHARM_NAME) --channel=edge  $(CHARM_NAME)-ui
	multipass exec $(MACHINE_NAME) -- juju deploy $(CHARM_NAME) --channel=edge --config charm-function=worker $(CHARM_NAME)-worker
	multipass exec $(MACHINE_NAME) -- juju deploy $(CHARM_NAME) --channel=edge --config charm-function=beat $(CHARM_NAME)-beat
	multipass exec $(MACHINE_NAME) -- juju wait-for application superset-k8s-beat --query='status=="blocked"' --timeout 10m

relate-ui:
	multipass exec $(MACHINE_NAME) -- juju relate $(CHARM_NAME)-ui postgresql-k8s
	multipass exec $(MACHINE_NAME) -- juju relate $(CHARM_NAME)-ui redis-k8s
	multipass exec $(MACHINE_NAME) -- juju relate $(CHARM_NAME)-ui nginx-ingress-integrator
	multipass exec $(MACHINE_NAME) -- juju wait-for application superset-k8s-ui --query='status=="active"' --timeout 10m

relate-worker:
	multipass exec $(MACHINE_NAME) -- juju relate $(CHARM_NAME)-worker postgresql-k8s
	multipass exec $(MACHINE_NAME) -- juju relate $(CHARM_NAME)-worker redis-k8s
	multipass exec $(MACHINE_NAME) -- juju wait-for application superset-k8s-worker --query='status=="active"' --timeout 10m

relate-beat:
	multipass exec $(MACHINE_NAME) -- juju relate $(CHARM_NAME)-beat postgresql-k8s
	multipass exec $(MACHINE_NAME) -- juju relate $(CHARM_NAME)-beat redis-k8s
	multipass exec $(MACHINE_NAME) -- juju wait-for application superset-k8s-beat --query='status=="active"' --timeout 10m

setup-ingress:
	multipass exec $(MACHINE_NAME) -- openssl genrsa -out server.key 2048
	multipass exec $(MACHINE_NAME) -- openssl req -new -key server.key -out server.csr -subj "/CN=superset-k8s"
	multipass exec $(MACHINE_NAME) -- openssl x509 -req -days 365 -in server.csr -signkey server.key -out server.crt -ext $(INGRESS_DNS)
	multipass exec $(MACHINE_NAME) -- kubectl create secret tls superset-tls --cert=server.crt --key=server.key

deploy-ingress:
	multipass exec $(MACHINE_NAME) -- juju deploy nginx-ingress-integrator --channel=edge --revision=71 --trust

remove-charms:
	multipass exec $(MACHINE_NAME) -- juju remove-application postgresql-k8s redis-k8s superset-k8s-ui superset-k8s-worker superset-k8s-beat nginx-ingress-integrator --force

remove-model:
	multipass exec $(MACHINE_NAME) -- juju destroy-model superset-k8s --release-storage --force --no-wait

remove-vm:
	multipass delete $(MACHINE_NAME)

purge:
	multipass purge
