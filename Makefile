# Локальные команды без source .venv/bin/activate
VENV ?= .venv
PY   := $(VENV)/bin/python
PIP  := $(VENV)/bin/pip

.DEFAULT_GOAL := help

.PHONY: help install venv serve up down reload reset-password verify-client test-backend debug-pool

help: ## Показать цели
	@grep -E '^[a-zA-Z0-9_.-]+:.*##' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*## "}; {printf "  \033[36m%-18s\033[0m %s\n", $$1, $$2}'

$(PY):
	python3 -m venv $(VENV)

install: $(PY) ## Создать .venv и установить зависимости
	$(PIP) install -q -r requirements.txt

venv: install ## То же, что install

serve: install ## Веб-админка (одноразовые логин/пароль в консоли)
	$(PY) -m admin serve

up: install ## Docker: поднять PgBouncer
	$(PY) -m admin up

down: install ## Docker: остановить PgBouncer
	$(PY) -m admin down

reload: install ## Пересобрать конфиг и SIGHUP reload
	$(PY) -m admin reload

reset-password: install ## make reset-password USER=vi
	@test -n "$(USER)" || (echo "Usage: make reset-password USER=<pgb_user>"; exit 1)
	$(PY) -m admin reset-password "$(USER)"

verify-client: install ## make verify-client USER=vi PASS='...'
	@test -n "$(USER)" || (echo "Usage: make verify-client USER=vi PASS='...'"; exit 1)
	@test -n "$(PASS)" || (echo "Usage: make verify-client USER=vi PASS='...'"; exit 1)
	$(PY) -m admin verify-client "$(USER)" -p "$(PASS)"

test-backend: install ## make test-backend POOL=pool_vi [VIA_DOCKER=1]
	@test -n "$(POOL)" || (echo "Usage: make test-backend POOL=pool_vi"; exit 1)
	$(PY) -m admin test-backend --pool "$(POOL)" $(if $(VIA_DOCKER),--via-docker,)

debug-pool: install ## make debug-pool POOL=pool_vi
	@test -n "$(POOL)" || (echo "Usage: make debug-pool POOL=pool_vi"; exit 1)
	$(PY) -m admin debug-pool "$(POOL)"
