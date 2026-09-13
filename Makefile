PYTHON=python3
IMAGE=micronote:latest

password:
	$(PYTHON) -c "import bcrypt; from getpass import getpass; print(bcrypt.hashpw(getpass().encode('utf-8'), bcrypt.gensalt()).decode('utf-8'))"

docker:
	mypy . --ignore-missing-imports
	docker build . -t $(IMAGE)

reload-fed:
	docker build . -t $(IMAGE)
	docker-compose -p instance2 -f docker-compose-tests.yml stop
	docker-compose -p instance1 -f docker-compose-tests.yml stop
	WEB_PORT=5006 CONFIG_DIR=./tests/fixtures/instance1/config DATA_DIR=./data/instance1 docker-compose -p instance1 -f docker-compose-tests.yml up -d --force-recreate --build
	WEB_PORT=5007 CONFIG_DIR=./tests/fixtures/instance2/config DATA_DIR=./data/instance2 docker-compose -p instance2 -f docker-compose-tests.yml up -d --force-recreate --build

reload-dev:
	docker-compose -f docker-compose-dev.yml up -d --force-recreate

update:
	hg pull -u
	docker build . -t $(IMAGE)
	docker-compose stop
	docker-compose up -d --force-recreate --build
