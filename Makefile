MACHINE_NAME = superset-vm
REPO_FOLDER = superset-k8s-operator
REPO = https://github.com/canonical/$(REPO_FOLDER).git

.PHONY: dev build install

dev: setup-multipass install-microk8s install-charmcraft install-juju clone

build: pack

install: deploy-charm deploy-redis deploy-postgres

install: 

setup-multipass:
	multipass launch -n $(MACHINE_NAME) -m 8G -c 4 -d 30G charm-dev

install-microk8s:
	multipass exec $(MACHINE_NAME) -- sudo snap install microk8s --channel=1.27-strict/stable
	multipass exec $(MACHINE_NAME) -- sudo snap alias microk8s.kubectl kubectl
	multipass exec $(MACHINE_NAME) -- bash -c 'sudo usermod -a -G snap_microk8s $$(whoami)'
	multipass exec $(MACHINE_NAME) -- microk8s status --wait-ready
	multipass exec $(MACHINE_NAME) -- sudo microk8s.enable dns rbac hostpath-storage
	multipass exec $(MACHINE_NAME) -- bash -c "microk8s.kubectl rollout status deployments/coredns -n kube-system -w --timeout=600s; \
	microk8s.kubectl rollout status deployments/hostpath-provisioner -n kube-system -w --timeout=600s"
	
install-charmcraft:
	multipass exec $(MACHINE_NAME) -- sudo snap install lxd --classic --channel=5.0/stable
	multipass exec $(MACHINE_NAME) -- sudo snap install charmcraft --classic --channel=latest/stable
	multipass exec $(MACHINE_NAME) -- lxd init --auto

install-juju:
	multipass exec $(MACHINE_NAME) -- sudo snap install juju --channel=3.1/stable
	multipass exec $(MACHINE_NAME) -- mkdir -p /home/ubuntu/.local/share/juju
	multipass exec $(MACHINE_NAME) -- juju bootstrap microk8s superset-controller
	multipass exec $(MACHINE_NAME) -- juju add-model superset-k8s
	multipass exec $(MACHINE_NAME) -- juju model-config logging-config="<root>=INFO;unit=DEBUG"
	multipass exec $(MACHINE_NAME) -- juju model-config update-status-hook-interval=1m
	multipass exec $(MACHINE_NAME) -- juju debug-log

clone:
	multipass exec $(MACHINE_NAME) -- bash -c '[ -d "$(REPO_FOLDER)/.git" ] || git clone $(REPO)'

pack:
	multipass exec $(MACHINE_NAME) -- bash -c 'cd $(REPO_FOLDER) && charmcraft pack'

deploy-charm:
	multipass exec $(MACHINE_NAME) -- juju deploy /home/ubuntu/$(REPO_FOLDER)/superset-k8s_ubuntu-22.04-amd64.charm --resource superset-image=apache/superset:2.1.0 superset-k8s
	multipass exec $(MACHINE_NAME) -- juju deploy /home/ubuntu/$(REPO_FOLDER)/superset-k8s_ubuntu-22.04-amd64.charm --resource superset-image=apache/superset:2.1.0 --config charm-function=worker superset-k8s-worker
	multipass exec $(MACHINE_NAME) -- juju deploy /home/ubuntu/$(REPO_FOLDER)/superset-k8s_ubuntu-22.04-amd64.charm --resource superset-image=apache/superset:2.1.0 --config charm-function=beat superset-k8s-beat

deploy-redis:
	multipass exec $(MACHINE_NAME) -- juju deploy redis-k8s --channel edge
	multipass exec $(MACHINE_NAME) -- juju relate redis-k8s superset-k8s

deploy-postgres:
	multipass exec $(MACHINE_NAME) -- juju deploy postgresql-k8s --channel 14/stable
	multipass exec $(MACHINE_NAME) -- juju relate postgresql-k8s superset-k8s
