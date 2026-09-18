.PHONY: setup build dev run seed test cli

setup:        ## install Python and Node dependencies
	uv sync --group dev
	npm install

build:        ## build CSS, JS, and vendor htmx
	npm run build

dev:          ## watch-rebuild frontend and run the server with reload
	npm run vendor
	npm run watch:css & npm run watch:js & \
	uv run uvicorn app.main:app --reload --port 8000; kill 0

run:          ## run the server (expects a prior `make build`)
	uv run uvicorn app.main:app --port 8000

seed:         ## insert a sample document with annotations
	uv run python scripts/seed.py

test:
	uv run pytest -q

cli:          ## put the `ce` launcher on your PATH (symlink in ~/.local/bin)
	mkdir -p ~/.local/bin && ln -sf "$(CURDIR)/bin/ce" ~/.local/bin/ce && echo "installed ~/.local/bin/ce"
