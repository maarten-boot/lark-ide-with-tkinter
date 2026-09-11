# lark-ide - development tasks
#
# The test suite drives a real Tk window. Under a desktop session it uses your display;
# with no DISPLAY set it falls back to xvfb-run, so `make test` works over ssh too.

PYTHON      ?= python3
RUFF        ?= ruff
LINE_LENGTH ?= 120

APP     := lark-ide.py
LIB     := lark_railroad.py
TESTS   := test_lark_ide.py
SOURCES := $(APP) $(LIB) $(TESTS)

# A throwaway HOME so a test run can never touch a real ~/.lark-ide/.
TEST_HOME := $(shell mktemp -d)
ifeq ($(DISPLAY),)
DISPLAY_WRAPPER := xvfb-run -a
else
DISPLAY_WRAPPER :=
endif

.DEFAULT_GOAL := help

.PHONY: help
help: ## Show this help
	@echo "lark-ide targets:"
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) \
		| awk 'BEGIN {FS = ":.*?## "}; {printf "  \033[36m%-12s\033[0m %s\n", $$1, $$2}'

.PHONY: run
run: ## Start the application
	$(PYTHON) $(APP)

.PHONY: format
format: ## Reformat the sources
	$(RUFF) format --line-length $(LINE_LENGTH) $(SOURCES)

.PHONY: lint
lint: ## Report lint problems without changing anything
	$(RUFF) check --line-length $(LINE_LENGTH) $(SOURCES)

.PHONY: fix
fix: ## Apply the lint fixes ruff can make safely, then reformat
	$(RUFF) check --line-length $(LINE_LENGTH) --fix $(SOURCES)
	$(RUFF) format --line-length $(LINE_LENGTH) $(SOURCES)

.PHONY: format-check
format-check: ## Fail if anything is not formatted
	$(RUFF) format --check --line-length $(LINE_LENGTH) $(SOURCES)

.PHONY: test
test: ## Run the test suite
	HOME=$(TEST_HOME) $(DISPLAY_WRAPPER) $(PYTHON) $(TESTS)

.PHONY: check
check: format-check lint test ## Everything CI should run

.PHONY: deps
deps: ## Install what the application and the tests need
	$(PYTHON) -m pip install lark ruff
	@$(PYTHON) -c "import tkinter" 2>/dev/null \
		|| echo "tkinter is missing: install python3-tk (Debian/Ubuntu) or tk (macOS/Homebrew)"

.PHONY: clean
clean: ## Remove caches and byte-compiled files
	rm -rf __pycache__ .ruff_cache .pytest_cache
	find . -name '*.pyc' -delete
