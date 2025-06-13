import os
import logging
import uuid
import time
from flask import Flask, request, jsonify, Response, g, has_app_context
import requests
from requests.exceptions import RequestException, HTTPError, ConnectionError, Timeout, SSLError, JSONDecodeError
from dotenv import load_dotenv
try:
    import brotli
except ImportError:
    brotli = None

# Load environment variables from .env file
load_dotenv()

app = Flask(__name__)

# === Logging Setup ===
class RequestContextFilter(logging.Filter):
    def filter(self, record):
        if has_app_context():
            record.request_id = getattr(g, "request_id", "none")
            record.client_ip = getattr(g, "client_ip", "unknown")
        else:
            record.request_id = "none"
            record.client_ip = "unknown"
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
    "FLASK_PORT": int(os.getenv("FLASK_PORT", "8868")),
    "FLASK_DEBUG": os.getenv("FLASK_DEBUG", "False").lower() == "true",
}

# Validate proxy configuration
if not (CONFIG["SOCKS5_PROXY_HOST"] and CONFIG["SOCKS5_PROXY_PORT"]):
    logger.error("SOCKS5_PROXY_HOST and SOCKS5_PROXY_PORT must be set in .env")
    raise ValueError("SOCKS5_PROXY_HOST and SOCKS5_PROXY_PORT must be set in .env")

# Compose proxy URL with or without auth
if CONFIG["SOCKS5_PROXY_USERNAME"] and CONFIG["SOCKS5_PROXY_PASSWORD"]:
    proxy_auth = f"{CONFIG['SOCKS5_PROXY_USERNAME']}:{CONFIG['SOCKS5_PROXY_PASSWORD']}@"
else:
    proxy_auth = ""

proxy_addr = (
    f"socks5h://{proxy_auth}{CONFIG['SOCKS5_PROXY_HOST']}:{CONFIG['SOCKS5_PROXY_PORT']}"
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
    if not auth:
        logger.warning("Missing Authorization header")
    return headers

# === Proxy request function ===
def proxy_request(method, endpoint, headers=None, json_data=None, params=None, stream=False):
    if not request.headers.get("Authorization"):
        logger.error("Authorization header is required")
        return jsonify({"error": "Authorization header is required"}), 401

    headers = headers or forwarded_headers()
    if json_data:
        headers["Content-Type"] = "application/json"

    full_url = f"{CONFIG['OPENAI_BASE_URL']}/{endpoint}"
    logger.info(f"Proxying {method} request to {full_url}")

    start_time = time.time()
    try:
        response = proxy_session.request(
            method=method,
            url=full_url,
            headers=headers,
            json=json_data,
            params=params,
            stream=stream,
            timeout=30,
        )
        elapsed_time = time.time() - start_time
        logger.info(f"Request completed in {elapsed_time:.2f}s, status: {response.status_code}")

        response.raise_for_status()

        if stream:
            def stream_response():
                for chunk in response.iter_lines():
                    if chunk:
                        yield chunk + b"\n\n"
            return Response(stream_response(), mimetype="text/event-stream")

        try:
            response_json = response.json()
            return jsonify(response_json)
        except JSONDecodeError as e:
            logger.error(f"Failed to parse JSON response: {str(e)}")
            error_response = {"error": f"Invalid JSON response: {str(e)}"}
            try:
                raw_text = response.content.decode('utf-8')[:400]
                error_response["raw_content"] = raw_text
            except UnicodeDecodeError:
                error_response["raw_content"] = str(response.content)[:400]
            return jsonify(error_response), 500

    except SSLError as e:
        elapsed_time = time.time() - start_time
        logger.error(f"SSL error in {endpoint} after {elapsed_time:.2f}s: {str(e)}")
        return jsonify({"error": f"SSL error: {str(e)}"}), 502
    except HTTPError as e:
        elapsed_time = time.time() - start_time
        status = e.response.status_code if e.response else 500
        logger.error(f"HTTP error in {endpoint} after {elapsed_time:.2f}s: {str(e)}")
        return jsonify({"error": str(e)}), status
    except ConnectionError as e:
        elapsed_time = time.time() - start_time
        logger.error(f"Connection error in {endpoint} after {elapsed_time:.2f}s: {str(e)}")
        return jsonify({"error": "Connection failed"}), 503
    except Timeout as e:
        elapsed_time = time.time() - start_time
        logger.error(f"Timeout error in {endpoint} after {elapsed_time:.2f}s: {str(e)}")
        return jsonify({"error": "Request timed out"}), 504
    except RequestException as e:
        elapsed_time = time.time() - start_time
        logger.error(f"Request error in {endpoint} after {elapsed_time:.2f}s: {str(e)}")
        return jsonify({"error": str(e)}), 500
    except ValueError as e:
        elapsed_time = time.time() - start_time
        logger.error(f"Invalid JSON data in {endpoint} after {elapsed_time:.2f}s: {str(e)}")
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
            logger.error("Request body must be JSON")
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
    with app.app_context():
        logger.info(f"Starting Flask server on {CONFIG['FLASK_HOST']}:{CONFIG['FLASK_PORT']}")
    if CONFIG["FLASK_DEBUG"]:
        app.run(
            host=CONFIG['FLASK_HOST'],
            port=CONFIG['FLASK_PORT'],
            debug=CONFIG['FLASK_DEBUG'],
        )
    else:
        logger.info(
            "Production mode detected. Please run with a WSGI server like Gunicorn "
            "(e.g., 'gunicorn -w 4 -b 0.0.0.0:8868 openai_proxy:app')."
        )