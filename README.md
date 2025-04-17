# OpenAI API Proxy with SOCKS5 Support

This is a Flask-based proxy server that forwards requests to the OpenAI API through a SOCKS5 proxy. It supports both regular and streaming requests and preserves request headers and authorization tokens.

## Features

- Proxies all OpenAI API requests under `/v1/*` paths.
- Supports GET and POST methods (including streaming via Server-Sent Events).
- Forwards client Authorization and other headers.
- Routes traffic through a configurable SOCKS5 proxy (with optional authentication).
- Adds request IDs and client IP info to logs for tracing.
- Simple setup using environment variables.

---

## Requirements

- Python 3.7+
- `pip` packages:
  - Flask
  - Requests with SOCKS support
  - python-dotenv

Install dependencies with:

```bash
pip install flask requests[socks] python-dotenv
```

---

## Configuration

Create a `.env` file in the project root with the following configurations:

```env
# OpenAI API base URL (default is official OpenAI API)
OPENAI_BASE_URL=https://api.openai.com/v1

# SOCKS5 proxy settings (required)
SOCKS5_PROXY_HOST=your.socks5.proxy.host
SOCKS5_PROXY_PORT=1080

# SOCKS5 proxy authentication (optional)
SOCKS5_PROXY_USERNAME=your_proxy_username
SOCKS5_PROXY_PASSWORD=your_proxy_password

# Flask server settings
FLASK_HOST=0.0.0.0
FLASK_PORT=8868

# Debug mode (set to True or False)
FLASK_DEBUG=False
```

- `SOCKS5_PROXY_USERNAME` and `SOCKS5_PROXY_PASSWORD` can be omitted if your SOCKS5 proxy does not require authentication.
- Adjust `FLASK_HOST` and `FLASK_PORT` for your deployment environment.

---

## Running the Proxy

Run the proxy using:

```bash
python openai_proxy.py
```

For production deployment, it is recommended to run with a WSGI server such as Gunicorn:

```bash
gunicorn -w 4 -b 0.0.0.0:8868 openai_proxy:app
```

---

## Usage

Send your OpenAI API requests to this proxy instead of directly to `api.openai.com`. For example:

**Request URL:**

```
http://localhost:8868/v1/chat/completions
```

**Headers:**

```
Authorization: Bearer YOUR_OPENAI_API_KEY
Content-Type: application/json
```

The proxy will forward the request through the configured SOCKS5 proxy and return the OpenAI API response transparently.

---

## Streaming Support

The proxy supports OpenAI streaming completions:

- When you include `"stream": true` in the request JSON body, the proxy will stream the response back to you as Server-Sent Events (SSE).
- The HTTP response will have the `Content-Type: text/event-stream` header.
- Clients should read chunks progressively to see partial generation results.

---

## Logging

- Each request is logged with a unique Request ID and the client IP address.
- Logs include info-level messages for proxied requests and warnings/errors for failures.

---

## Notes

- Ensure your SOCKS5 proxy is reachable and properly configured.
- The script requires the `requests[socks]` package and underlying `PySocks`.
- This proxy assumes clients provide the OpenAI `Authorization` header.
- The proxy does not currently implement rate limiting or additional authentication.

---

## License

This project is provided as-is without warranty. Adapt and use at your own risk.

---

Feel free to open issues or contribute improvements!