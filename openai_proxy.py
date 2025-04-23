import os
import logging
import uuid
from flask import Flask, request, jsonify, Response, g, has_app_context
import requests
from requests.exceptions import RequestException, HTTPError, ConnectionError, Timeout
from dotenv import load_dotenv
import json

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# === Logging Setup with Context ===

class RequestContextFilter(logging.Filter):
    def filter(self, record):
        record.request_id = getattr(g, "request_id", "none") if has_app_context() else "none"
        record.client_ip = getattr(g, "client_ip", "none") if has_app_context() else "none"
        return True

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - [RequestID: %(request_id)s] - [ClientIP: %(client_ip)s] - %(message)s"
)

logger = logging.getLogger(__name__)
for handler in logging.getLogger().handlers:
    handler.addFilter(RequestContextFilter())

@app.before_request
def add_request_context():
    g.request_id = str(uuid.uuid4())
    g.client_ip = request.remote_addr or "unknown"

# === Configuration ===
CONFIG = {
    "OPENAI_BASE_URL": os.getenv("OPENAI_BASE_URL", "https://api.openai.com/v1"),
    "SOCKS5_PROXY_HOST": os.getenv("SOCKS5_PROXY_HOST"),
    "SOCKS5_PROXY_PORT": os.getenv("SOCKS5_PROXY_PORT"),
    "SOCKS5_PROXY_USERNAME": os.getenv("SOCKS5_PROXY_USERNAME"),
    "SOCKS5_PROXY_PASSWORD": os.getenv("SOCKS5_PROXY_PASSWORD"),
    "FLASK_HOST": os.getenv("FLASK_HOST", "0.0.0.0"),
    "FLASK_PORT": int(os.getenv("FLASK_PORT", 8868)),
    "FLASK_DEBUG": os.getenv("FLASK_DEBUG", "False").lower() == "true",
}

# Validate proxy configuration
if not (CONFIG["SOCKS5_PROXY_HOST"] and CONFIG["SOCKS5_PROXY_PORT"]):
    logger.error("SOCKS5_PROXY_HOST and SOCKS5_PROXY_PORT must be set in .env")
    raise ValueError("SOCKS5_PROXY_HOST and SOCKS5_PROXY_PORT must be set in environment")

# Compose proxy URL with or without auth
if CONFIG["SOCKS5_PROXY_USERNAME"] and CONFIG["SOCKS5_PROXY_PASSWORD"]:
    proxy_auth = f"{CONFIG['SOCKS5_PROXY_USERNAME']}:{CONFIG['SOCKS5_PROXY_PASSWORD']}@"
else:
    proxy_auth = ""

proxy_addr = (
    f"socks5://{proxy_auth}{CONFIG['SOCKS5_PROXY_HOST']}:{CONFIG['SOCKS5_PROXY_PORT']}"
)

# Setup requests session with SOCKS5 proxy
proxy_session = requests.Session()
proxy_session.proxies = {
    "http": proxy_addr,
    "https": proxy_addr,
}

# === Helper to forward request headers ===

def forwarded_headers():
    headers = {}
    for k, v in request.headers.items():
        if k.lower() == "host":
            continue
        headers[k] = v
    auth = request.headers.get("Authorization")
    if auth:
        headers["Authorization"] = auth
    else:
        logger.warning("Missing Authorization header")
    return headers

# === Proxy request function ===

def proxy_request(method, endpoint, headers=None, json_data=None, params=None, stream=False):
    if not request.headers.get("Authorization"):
        logger.warning("Missing Authorization header")
        return jsonify({"error": "Authorization header is required"}), 401

    headers = headers or forwarded_headers()
    if json_data:
        headers["Content-Type"] = "application/json"

    logger.info(f"Proxying {method} request to {endpoint}")

    try:
        response = proxy_session.request(
            method=method,
            url=f"{CONFIG['OPENAI_BASE_URL']}/{endpoint}",
            headers=headers,
            json=json_data,
            params=params,
            stream=stream,
            timeout=30,
        )
        response.raise_for_status()

        if stream:
            def stream_response():
                for chunk in response.iter_lines():
                    if chunk:
                        yield chunk + b"\n\n"

            return Response(stream_response(), mimetype="text/event-stream")

        return jsonify(response.json())

    except HTTPError as e:
        status = e.response.status_code if e.response else 500
        logger.error(f"HTTP error in {endpoint}: {str(e)}")
        return jsonify({"error": str(e)}), status
    except ConnectionError as e:
        logger.error(f"Connection error in {endpoint}: {str(e)}")
        return jsonify({"error": "Connection failed"}), 503
    except Timeout as e:
        logger.error(f"Timeout error in {endpoint}: {str(e)}")
        return jsonify({"error": "Request timed out"}), 504
    except RequestException as e:
        logger.error(f"Request error in {endpoint}: {str(e)}")
        return jsonify({"error": str(e)}), 500
    except ValueError as e:
        logger.error(f"Invalid JSON data in {endpoint}: {str(e)}")
        return jsonify({"error": "Invalid JSON data"}), 400

# === Routes ===

@app.route("/v1/<path:path>", methods=["GET"])
def proxy_generic_get(path):
    return proxy_request(
        method="GET",
        endpoint=path,
        params=request.args,
    )

@app.route("/v1/<path:path>", methods=["POST"])
def proxy_generic_post(path):
    try:
        data = request.get_json()
        if data is None:
            return jsonify({"error": "Request body must be JSON"}), 400
        is_streaming = data.get("stream", False)
        return proxy_request(
            method="POST",
            endpoint=path,
            json_data=data,
            stream=is_streaming,
        )
    except ValueError as e:
        logger.error(f"Invalid JSON data in {path}: {str(e)}")
        return jsonify({"error": "Invalid JSON data"}), 400

# === Main ===

if __name__ == "__main__":
    if CONFIG["FLASK_DEBUG"]:
        logger.info(f"Starting Flask development server on {CONFIG['FLASK_HOST']}:{CONFIG['FLASK_PORT']}")
        app.run(
            host=CONFIG["FLASK_HOST"],
            port=CONFIG["FLASK_PORT"],
            debug=CONFIG["FLASK_DEBUG"],
        )
    else:
        logger.info(
            "Production mode detected. Please run with a WSGI server like Gunicorn "
            "(e.g., 'gunicorn -w 4 -b 0.0.0.0:8868 openai_proxy:app')."
        )