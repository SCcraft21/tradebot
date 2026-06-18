# Security Policy

## Supported Versions

We actively monitor and provide security patches for the following versions of TradeBot:

| Version | Supported          |
| ------- | ------------------ |
| 1.0.x   | ✅ Yes             |
| < 1.0.0 | ❌ No              |

Please ensure you are always running the latest stable patch release to protect your deployment.

## Reporting a Vulnerability

**Do not open public GitHub issues for security vulnerabilities.** If you discover a security vulnerability (such as API credential leaks, execution bypasses, or logic flaws that put capital at risk), please report it responsibly by following these steps:

1. Draft a detailed description of the vulnerability, including:
   * The specific component/file affected.
   * Steps to reproduce or proof-of-concept configuration.
   * Potential impact (e.g., unauthorized order execution, data leak).
2. Email your findings directly to the security team or maintainer at **[Your Security Email/Contact]**.
3. You will receive an acknowledgment of your report within 48 hours, along with a timeline for a fix.

## Critical Security Guidelines for Deployments

Because TradeBot interacts directly with live market accounts and cryptographic keys, you must strictly adhere to the following best practices:

### 1. API Key Permissions (Least Privilege)
* **Never** enable "Withdrawal" or "Transfer" permissions on the API keys generated from your exchange. TradeBot only requires **Read** and **Trade/Execute** permissions.
* Limit API key access to your specific deployment server's IP address using the exchange's IP whitelist feature.

### 2. Environment Variable Hygiene
* Ensure your `.env` file is explicitly listed in your `.gitignore` to prevent secret credentials from being accidentally committed to public version control.
* If deploying to a production server or cloud platform, inject keys natively via environment managers (e.g., AWS Secrets Manager, systemd environment variables) instead of storing raw `.env` files on disk.

### 3. Network Isolation
* If your bot utilizes a database or external webhooks, ensure they communicate over isolated internal networks or strictly encrypted TLS channels.
