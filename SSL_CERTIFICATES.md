# SSL/TLS Certificate Management

## Security Notice

**NEVER commit private keys or certificates to version control!**

The `.gitignore` file has been updated to prevent accidental commits of:
- `*.crt` - Certificate files
- `*.key` - Private key files  
- `*.pem` - PEM encoded certificates/keys
- `electrumdb/server.*` - Specific server certificate files

## Generating New Certificates

For development and testing, you can generate a self-signed certificate:

```bash
# Generate private key
openssl genrsa -out electrumdb/server.key 2048

# Generate certificate signing request
openssl req -new -key electrumdb/server.key -out electrumdb/server.csr

# Generate self-signed certificate
openssl x509 -req -days 365 -in electrumdb/server.csr -signkey electrumdb/server.key -out electrumdb/server.crt
```

## Production Deployment

For production environments:
1. Use certificates from a trusted CA (Let's Encrypt, DigiCert, etc.)
2. Store private keys securely (outside the repository)
3. Set appropriate file permissions (600 for private keys)
4. Consider using a secrets management system

## Configuration

Update your `.env` file to point to the correct certificate paths:
```
SSL_CERTFILE=/path/to/your/server.crt
SSL_KEYFILE=/path/to/your/server.key
```

## Security Best Practices

- Private keys should have restricted permissions (600)
- Never share private keys via email, chat, or version control
- Rotate certificates periodically
- Use strong encryption (2048-bit RSA or higher)
- Consider using certificate pinning for additional security
