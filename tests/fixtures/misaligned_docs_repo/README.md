# Misaligned Docs Repo

A README that overstates what the code actually does.

## Requirements

```
pip install rare-lib-xyz
```

## Configuration

Set the `MAGIC_TOKEN` environment variable before running:

```
export MAGIC_TOKEN=please
```

## Usage

Bootstrap and run with legacy mode if desired:

```
./run-everything.sh
python app.py --port 8080 --legacy-mode
```

The `--legacy-mode` flag enables the old execution path. The `--port` flag
accepts any integer between 1024 and 65535.
