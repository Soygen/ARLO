lock:
	rm uv.lock
	uv lock

env:
	rm uv.lock
	uv lock
	uv sync --all-extras

run:
	uv run arc-helper

calibrate:
	uv run arc-calibrate

db_list:
	uv run arc-db list

winbuild:
	python build.py

update-db:
	uv run python update_db.py

update-db-merge:
	uv run python update_db.py --merge

update-db-dry:
	uv run python update_db.py --dry-run
