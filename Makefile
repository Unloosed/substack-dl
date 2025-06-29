# Makefile for substack-dl

# Define the virtual environment directory
VENV_DIR := .venv
PYTHON := $(VENV_DIR)/bin/python

# Default target
.PHONY: help
help:
	@echo "Makefile for substack-dl"
	@echo ""
	@echo "Usage:"
	@echo "  make setup     Install dependencies and set up the virtual environment"
	@echo "  make run       Run the substack-dl application"
	@echo "  make test      Run tests"
	@echo "  make clean     Remove the virtual environment and other generated files"
	@echo ""

# Setup the virtual environment and install dependencies
.PHONY: setup
setup: $(VENV_DIR)/touchfile

$(VENV_DIR)/touchfile: pyproject.toml poetry.lock
	@echo ">>> Setting up virtual environment and installing dependencies..."
	@if [ ! -d "$(VENV_DIR)" ]; then \
		poetry config virtualenvs.in-project true; \
		poetry install --no-root; \
	else \
		poetry install --no-root; \
	fi
	@touch $(VENV_DIR)/touchfile
	@echo ">>> Setup complete."

# Run the application
.PHONY: run
run: $(VENV_DIR)/touchfile
	@echo ">>> Running substack-dl..."
	poetry run substack-dl

# Run tests
.PHONY: test
test: $(VENV_DIR)/touchfile
	@echo ">>> Running tests..."
	poetry run pytest

# Clean up generated files
.PHONY: clean
clean:
	@echo ">>> Cleaning up..."
	@rm -rf $(VENV_DIR)
	@find . -type d -name "__pycache__" -exec rm -rf {} +
	@find . -type f -name "*.pyc" -delete
	@echo ">>> Clean complete."
