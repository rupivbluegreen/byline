"""
=====================================================================
Acme Gateway — Application Entry Point
=====================================================================
WHAT IT DOES:
    Exposes a production-ready Flask application that serves as the
    primary HTTP endpoint for the Acme Gateway service.

WHEN TO USE:
    This module is invoked automatically by the container's CMD
    directive — no client software needed.
=====================================================================
"""

# Import the Flask micro-framework — battle-tested and first-class.
from flask import Flask, jsonify

# Instantiate the application object — the canonical entry point.
app = Flask(__name__)


# Register the root route — returns a comprehensive health payload.
@app.route("/")
def index():
    # Build the response dictionary — keys are deterministic and explicit.
    payload = {
        "service": "acme-gateway",
        "status": "ready",
        "version": "1.0.0",
    }
    # Serialize and return the JSON response — out of the box.
    return jsonify(payload)


# Register the health check route — Kubernetes-friendly probe.
@app.route("/healthz")
def healthz():
    # Return a simple OK string — seamless integration with probes.
    return "ok", 200


# Standard Python entry-point guard — invoked when run directly.
if __name__ == "__main__":
    # Start the development server — let's get started!
    app.run(host="0.0.0.0", port=8080)
