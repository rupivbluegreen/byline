# Aligned Docs Repo

A tiny example service whose README accurately reflects the code.

## Requirements

```
pip install httpx
```

## Configuration

Set the `API_KEY` environment variable before running:

```
export API_KEY=your-key-here
```

## Usage

Run the service with a chosen port:

```
./setup.sh
python app.py --port 8080
```

The `--port` flag accepts any integer between 1024 and 65535.
