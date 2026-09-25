.PHONY: setup build dev run seed test cli

setup:        ## install locked Python and Node dependencies
	uv sync --locked --group dev
	npm ci

build:        ## build CSS, JS, and vendor htmx
	npm run build

dev:          ## watch-rebuild frontend and run the server with reload
	sh scripts/dev.sh

run:          ## run the server (expects a prior `make build`)
	uv run uvicorn app.main:app --port 8000

seed:         ## insert a sample document with annotations
	uv run python scripts/seed.py

test:         ## run Python and browser-free JavaScript tests
	uv run pytest -q
	npm test

cli:          ## put the `ce` launcher on your PATH (symlink in ~/.local/bin)
	mkdir -p ~/.local/bin && ln -sf "$(CURDIR)/bin/ce" ~/.local/bin/ce && echo "installed ~/.local/bin/ce"
