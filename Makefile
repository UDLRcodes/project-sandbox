BIN ?= $(HOME)/.local/bin
DATA ?= $(HOME)/.local/share/project-sandbox
VENV := $(DATA)/venv
NAME := ,project-sandbox

.PHONY: test lint format typecheck coverage install uninstall
test:
	.venv/bin/pytest -q

# Mirror the CI gates locally.
lint:
	.venv/bin/ruff check .
	.venv/bin/ruff format --check .

format:
	.venv/bin/ruff check --fix .
	.venv/bin/ruff format .

typecheck:
	.venv/bin/mypy project_sandbox.py

coverage:
	.venv/bin/pytest -m "not slow" --cov=project_sandbox --cov-report=term-missing --cov-fail-under=90

# Installs a self-contained copy: a private venv (with PyYAML) plus the script,
# whose shebang is rewritten to that venv's python. Touches nothing system-wide.
install:
	mkdir -p "$(BIN)" "$(DATA)"
	python3 -m venv "$(VENV)"
	"$(VENV)/bin/pip" install -q --upgrade pip PyYAML
	printf '#!%s/bin/python\n' "$(VENV)" > "$(BIN)/$(NAME)"
	tail -n +2 project_sandbox.py >> "$(BIN)/$(NAME)"
	chmod +x "$(BIN)/$(NAME)"
	@echo "Installed $(NAME) to $(BIN) (private venv at $(VENV))"
	@echo "Ensure $(BIN) is on PATH."

uninstall:
	rm -f "$(BIN)/$(NAME)"
	rm -rf "$(VENV)"
	@echo "Removed $(NAME) and its venv."
