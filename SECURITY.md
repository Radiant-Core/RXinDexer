# Security Policy

## Supported Versions

| Version | Supported          |
| ------- | ------------------ |
| 1.17.x  | :white_check_mark: |
| < 1.17  | :x:                |

## Reporting a Vulnerability

### How to Report

1. **Do NOT** create a public GitHub issue for security vulnerabilities
2. Email security concerns to the maintainers
3. Include:
   - Description of the vulnerability
   - Steps to reproduce
   - Potential impact
   - Server configuration (if relevant)

### Response Timeline

- **Initial Response:** Within 48 hours
- **Status Update:** Within 7 days
- **Resolution Target:** Within 30 days for critical issues

## Security Considerations

### Server Exposure

ElectrumX is a public-facing server. Security considerations:

1. **Rate Limiting:** Enable rate limiting to prevent DoS
2. **Firewall:** Use firewall rules to limit access
3. **SSL/TLS:** Always use SSL for public servers
4. **Resource Limits:** Configure memory and connection limits

### Configuration Hardening

See `docs/SECURITY_HARDENING.md` for detailed hardening guide.

Essential settings:
```bash
# Rate limiting (required for public servers)
COST_SOFT_LIMIT=1000
COST_HARD_LIMIT=10000

# Connection limits
MAX_SESSIONS=1000
SESSION_TIMEOUT=600

# SSL (required for production)
SSL_CERTFILE=/path/to/cert.pem
SSL_KEYFILE=/path/to/key.pem
```

### RPC Security

1. **Change Default Password:** Never use default RPC credentials
2. **Bind to Localhost:** Only expose RPC to localhost unless necessary
3. **Use Authentication:** Always require authentication for RPC

### Known Limitations

1. **Websockets Library:** Pinned version due to API break
2. **Memory Usage:** Can consume significant memory with full index
3. **DoS Potential:** Without rate limiting, vulnerable to resource exhaustion

## Dependencies

- Python 3.8+
- aiohttp, websockets, plyvel/rocksdb

Keep dependencies updated and monitor for security advisories.

---

*Last updated: January 2026*
